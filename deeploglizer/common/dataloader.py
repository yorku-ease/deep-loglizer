"""
The interface to load log datasets. The datasets currently supported include
HDFS and BGL.

Authors:
    LogPAI Team

"""

import logging
import pandas as pd
import os
import numpy as np
import re
import pickle
import json
from collections import OrderedDict, defaultdict
from torch.utils.data import Dataset
from datetime import datetime

from deeploglizer.common.utils import decision

def load_sessions(data_dir):
    with open(os.path.join(data_dir, "data_desc.json"), "r") as fr:
        data_desc = json.load(fr)
    with open(os.path.join(data_dir, "session_train.pkl"), "rb") as fr:
        session_train = pickle.load(fr)
    with open(os.path.join(data_dir, "session_test.pkl"), "rb") as fr:
        session_test = pickle.load(fr)

    train_labels = [
        v["label"] if not isinstance(v["label"], list) else int(sum(v["label"]) > 0)
        for _, v in session_train.items()
    ]
    test_labels = [
        v["label"] if not isinstance(v["label"], list) else int(sum(v["label"]) > 0)
        for _, v in session_test.items()
    ]

    num_train = len(session_train)
    ratio_train = sum(train_labels) / num_train
    num_test = len(session_test)
    ratio_test = sum(test_labels) / num_test
    logging.info("Load from {}".format(data_dir))
    logging.info(json.dumps(data_desc, indent=4))
    logging.info(
        "# train sessions {} ({:.2f} anomalies)".format(num_train, ratio_train)
    )
    logging.info("# test sessions {} ({:.2f} anomalies)".format(num_test, ratio_test))
    return session_train, session_test


class log_dataset(Dataset):
    def __init__(self, session_dict, feature_type="semantics"):
        flatten_data_list = []
        # flatten all sessions
        for session_idx, data_dict in enumerate(session_dict.values()):
            features = data_dict["features"][feature_type]
            window_labels = data_dict["window_labels"]
            window_anomalies = data_dict["window_anomalies"]
            for window_idx in range(len(window_labels)):
                sample = {
                    "session_idx": session_idx,  # not session id
                    "features": features[window_idx],
                    "window_labels": window_labels[window_idx],
                    "window_anomalies": window_anomalies[window_idx],
                }
                flatten_data_list.append(sample)
        self.flatten_data_list = flatten_data_list

    def __len__(self):
        return len(self.flatten_data_list)

    def __getitem__(self, idx):
        return self.flatten_data_list[idx]


def load_BGL(
    log_file,
    train_ratio=None,
    test_ratio=0.8,
    train_anomaly_ratio=0,
    random_partition=False,
    filter_normal=True,
    **kwargs
):
    logging.info("Loading BGL logs from {}.".format(log_file))
    struct_log = pd.read_csv(log_file, engine="c", na_filter=False, memory_map=True)
    # struct_log.sort_values(by=["Timestamp"], inplace=True)
    logging.info("{} lines loaded.".format(struct_log.shape[0]))

    templates = struct_log["EventTemplate"].values
    labels = struct_log["Label"].map(lambda x: x != "-").astype(int).values

    total_indice = np.array(list(range(templates.shape[0])))
    if random_partition:
        logging.info("Using random partition.")
        np.random.shuffle(total_indice)

    if train_ratio is None:
        train_ratio = 1 - test_ratio
    assert train_ratio + test_ratio <= 1, "train_ratio + test_ratio should <= 1."
    train_lines = int(train_ratio * len(total_indice))
    test_lines = int(test_ratio * len(total_indice))

    idx_train = total_indice[0:train_lines]
    idx_test = total_indice[-test_lines:]

    idx_train = [
        idx
        for idx in idx_train
        if (labels[idx] == 0 or (labels[idx] == 1 and decision(train_anomaly_ratio)))
    ]

    if filter_normal:
        logging.info(
            "Filtering unseen normal tempalates in {} test data.".format(len(idx_test))
        )
        seen_normal = set(templates[idx_train].tolist())
        idx_test = [
            idx
            for idx in idx_test
            if not (labels[idx] == 0 and (templates[idx] not in seen_normal))
        ]

    session_train = {
        "all": {"templates": templates[idx_train].tolist(), "label": labels[idx_train]}
    }
    session_test = {
        "all": {"templates": templates[idx_test].tolist(), "label": labels[idx_test]}
    }

    labels_train = labels[idx_train]
    labels_test = labels[idx_test]

    train_anomaly = 100 * sum(labels_train) / len(labels_train)
    test_anomaly = 100 * sum(labels_test) / len(labels_test)

    logging.info("# train lines: {} ({:.2f}%)".format(len(labels_train), train_anomaly))
    logging.info("# test lines: {} ({:.2f}%)".format(len(labels_test), test_anomaly))

    return session_train, session_test


def load_OpenStack(
    log_file,
    label_file,
    train_ratio=None,
    test_ratio=None,
    train_anomaly_ratio=1,
    random_partition=False,
    **kwargs
):
    """Load HDFS structured log into train and test data

    Arguments
    ---------
        TODO

    Returns
    -------
        TODO
    """
    logging.info("Loading OpenStack logs from {}.".format(log_file))
    struct_log = pd.read_csv(log_file, engine="c", na_filter=False, memory_map=True)
    # struct_log.sort_values(by=["Date", "Time"], inplace=True)

    # assign labels
    label_data = pd.read_csv(label_file, engine="c", na_filter=False, memory_map=True)
    label_data["Label"] = label_data["Label"].map(lambda x: int(x == "Anomaly"))
    label_data_dict = dict(zip(label_data["Datetime"], label_data["Label"]))

    session_dict = OrderedDict()
    column_idx = {col: idx for idx, col in enumerate(struct_log.columns)}


    for _, row in enumerate(struct_log.values):
        datetime_string = re.search('(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', row[column_idx['Date']] + ' ' + row[column_idx['Time']]).group()

        datetime_object = datetime.strptime(datetime_string, '%Y-%m-%d %H:%M:%S')
        second = datetime_object.second
        if second <= 30:
            datetime_string = str(datetime_object.replace(second=0))
        else:
            datetime_string = str(datetime_object.replace(second=30))
        if not datetime_string in session_dict:
            session_dict[datetime_string] = defaultdict(list)
        session_dict[datetime_string]["templates"].append(row[column_idx['EventTemplate']].replace('[',''))

    for k in session_dict.keys():
        session_dict[k]["label"] = label_data_dict[k]


    session_idx = list(range(len(session_dict)))
    # split data
    if random_partition:
        logging.info("Using random partition.")
        np.random.shuffle(session_idx)

    session_ids = np.array(list(session_dict.keys()))
    

    session_labels = np.array(list(map(lambda x: label_data_dict[x], session_ids)))

    if train_ratio is None:
        train_ratio = 1 - test_ratio
    train_lines = int(train_ratio * len(session_idx))
    test_lines = int(test_ratio * len(session_idx))

    session_idx_train = session_idx[0:train_lines]
    session_idx_test = session_idx[-test_lines:]

    session_id_train = session_ids[session_idx_train]
    session_id_test = session_ids[session_idx_test]
    session_labels_train = session_labels[session_idx_train]
    session_labels_test = session_labels[session_idx_test]

    logging.info("Total # sessions: {}".format(len(session_ids)))

    session_train = {
        k: session_dict[k]
        for k in session_id_train
        if (session_dict[k]["label"] == 0)
        or (session_dict[k]["label"] == 1 and decision(train_anomaly_ratio))
    }

    session_test = {k: session_dict[k] for k in session_id_test}

    session_labels_train = [v["label"] for k, v in session_train.items()]
    session_labels_test = [v["label"] for k, v in session_test.items()]

    train_anomaly = 100 * sum(session_labels_train) / len(session_labels_train)
    test_anomaly = 100 * sum(session_labels_test) / len(session_labels_test)

    logging.info(
        "# train sessions: {} ({:.2f}%)".format(len(session_train), train_anomaly)
    )
    logging.info(
        "# test sessions: {} ({:.2f}%)".format(len(session_test), test_anomaly)
    )

    return session_train, session_test


def load_HDFS_semantic(log_semantic_path):
    train = os.path.join(log_semantic_path, "session_train.pkl")
    test = os.path.join(log_semantic_path, "session_test.pkl")

    with open(train, "rb") as fr:
        session_train = pickle.load(fr)

    with open(test, "rb") as fr:
        session_test = pickle.load(fr)

    # session_test = {k: v for i, (k, v) in enumerate(session_test.items()) if i < 50000}
    logging.info(
        "# train sessions: {}, # test sessions: {}".format(
            len(session_train), len(session_test)
        )
    )
    return session_train, session_test


def load_HDFS_id(log_id_path):
    train = os.path.join(log_id_path, "hdfs_train")
    test_normal = os.path.join(log_id_path, "hdfs_test_normal")
    test_anomaly = os.path.join(log_id_path, "hdfs_test_abnormal")

    session_train = {}
    for idx, line in enumerate(open(train)):
        sample = {"templates": line.split(), "label": 0}
        session_train[idx] = sample

    session_test = {}
    for idx, line in enumerate(open(test_normal)):
        if idx > 50000:
            break
        sample = {"templates": line.split(), "label": 0}
        session_test[idx] = sample

    for idx, line in enumerate(open(test_anomaly), len(session_test)):
        if idx > 100000:
            break
        sample = {"templates": line.split(), "label": 1}
        session_test[idx] = sample

    logging.info(
        "# train sessions: {}, # test sessions: {}".format(
            len(session_train), len(session_test)
        )
    )

    # logging.info("# test sessions: {} ({:.2f}%)".format(len(session_test), test_anomaly_ratio))
    return session_train, session_test

def load_HDFS(
    log_file,
    label_file,
    train_ratio=None,
    test_ratio=None,
    train_anomaly_ratio=1,
    random_partition=False,
    **kwargs
):
    """Load HDFS structured log into train and test data

    Arguments
    ---------
        TODO

    Returns
    -------
        TODO
    """
    logging.info("Loading HDFS logs from {}.".format(log_file))
    struct_log = pd.read_csv(log_file, engine="c", na_filter=False, memory_map=True)
    # struct_log.sort_values(by=["Date", "Time"], inplace=True)

    # assign labels
    label_data = pd.read_csv(label_file, engine="c", na_filter=False, memory_map=True)
    label_data["Label"] = label_data["Label"].map(lambda x: int(x == "Anomaly"))
    label_data_dict = dict(zip(label_data["BlockId"], label_data["Label"]))

    session_dict = OrderedDict()
    column_idx = {col: idx for idx, col in enumerate(struct_log.columns)}
    for _, row in enumerate(struct_log.values):
        blkId_list = re.findall(r"(blk_-?\d+)", row[column_idx["Content"]])
        blkId_set = set(blkId_list)
        for blk_Id in blkId_set:
            if blk_Id not in session_dict:
                session_dict[blk_Id] = defaultdict(list)
            session_dict[blk_Id]["templates"].append(row[column_idx["EventTemplate"]])

    for k in session_dict.keys():
        session_dict[k]["label"] = label_data_dict[k]

    session_idx = list(range(len(session_dict)))
    # split data
    if random_partition:
        logging.info("Using random partition.")
        np.random.shuffle(session_idx)

    session_ids = np.array(list(session_dict.keys()))
    session_labels = np.array(list(map(lambda x: label_data_dict[x], session_ids)))

    if train_ratio is None:
        train_ratio = 1 - test_ratio
    train_lines = int(train_ratio * len(session_idx))
    test_lines = int(test_ratio * len(session_idx))

    session_idx_train = session_idx[0:train_lines]
    session_idx_test = session_idx[-test_lines:]

    session_id_train = session_ids[session_idx_train]
    session_id_test = session_ids[session_idx_test]
    session_labels_train = session_labels[session_idx_train]
    session_labels_test = session_labels[session_idx_test]

    logging.info("Total # sessions: {}".format(len(session_ids)))

    session_train = {
        k: session_dict[k]
        for k in session_id_train
        if (session_dict[k]["label"] == 0)
        or (session_dict[k]["label"] == 1 and decision(train_anomaly_ratio))
    }

    session_test = {k: session_dict[k] for k in session_id_test}

    session_labels_train = [v["label"] for k, v in session_train.items()]
    session_labels_test = [v["label"] for k, v in session_test.items()]

    train_anomaly = 100 * sum(session_labels_train) / len(session_labels_train)
    test_anomaly = 100 * sum(session_labels_test) / len(session_labels_test)

    logging.info(
        "# train sessions: {} ({:.2f}%)".format(len(session_train), train_anomaly)
    )
    logging.info(
        "# test sessions: {} ({:.2f}%)".format(len(session_test), test_anomaly)
    )

    return session_train, session_test
