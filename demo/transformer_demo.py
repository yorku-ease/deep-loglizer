#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
sys.path.append("../")
import argparse
from torch.utils.data import DataLoader
from deeploglizer.models import Transformer
from deeploglizer.common.preprocess import FeatureExtractor
from deeploglizer.common.dataloader import load_sessions, log_dataset, load_OpenStack
from deeploglizer.common.utils import seed_everything, dump_final_results, dump_params


parser = argparse.ArgumentParser()

##### Model params
parser.add_argument("--model_name", default="Transformer", type=str)
parser.add_argument("--hidden_size", default=128, type=int)
parser.add_argument("--num_layers", default=2, type=int)
parser.add_argument("--embedding_dim", default=32, type=int)
parser.add_argument("--nhead", default=2, type=int)

##### Dataset params
parser.add_argument("--dataset", default="HDFS", type=str)
parser.add_argument(
    "--data_dir", default="../data/processed/HDFS_100k/hdfs_0.0_tar", type=str
)
parser.add_argument("--window_size", default=10, type=int)
parser.add_argument("--stride", default=1, type=int)

##### Input params
parser.add_argument("--feature_type", default="sequentials", type=str)
parser.add_argument("--use_attention", action="store_true")
parser.add_argument("--label_type", default="next_log", type=str)
parser.add_argument("--use_tfidf", action="store_true")
parser.add_argument("--max_token_len", default=50, type=int)
parser.add_argument("--min_token_count", default=1, type=int)
# Uncomment the following to use pretrained word embeddings. The "embedding_dim" should be set as 300
# parser.add_argument(
#     "--pretrain_path", default="../data/pretrain/wiki-news-300d-1M.vec", type=str
# )

##### Training params
parser.add_argument("--epoches", default=100, type=int)
parser.add_argument("--batch_size", default=1024, type=int)
parser.add_argument("--learning_rate", default=0.01, type=float)
parser.add_argument("--topk", default=10, type=int)
parser.add_argument("--patience", default=3, type=int)

##### Others
parser.add_argument("--random_seed", default=42, type=int)
parser.add_argument("--gpu", default=0, type=int)

params = vars(parser.parse_args())

model_save_path = dump_params(params)

struct_log = '../../log-anomaly-benchmark/OpenStack_structured/OpenStack_main_structured.csv' # The structured log file
label_file = '../../log-anomaly-benchmark/processed/datetime.csv' # The anomaly label file

if __name__ == "__main__":
    seed_everything(params["random_seed"])

    session_train, session_test = load_OpenStack(log_file=struct_log, label_file=label_file, test_ratio=0.5, random_partition=True)
    ext = FeatureExtractor(**params)

    session_train = ext.fit_transform(session_train)
    session_test = ext.transform(session_test, datatype="test")

    dataset_train = log_dataset(session_train, feature_type=params["feature_type"])
    dataloader_train = DataLoader(
        dataset_train, batch_size=params["batch_size"], shuffle=True, pin_memory=True
    )

    dataset_test = log_dataset(session_test, feature_type=params["feature_type"])
    dataloader_test = DataLoader(
        dataset_test, batch_size=4096, shuffle=False, pin_memory=True
    )

    model = Transformer(
        meta_data=ext.meta_data, model_save_path=model_save_path, **params
    )

    eval_results = model.fit(
        dataloader_train,
        test_loader=dataloader_test,
        epoches=params["epoches"],
        learning_rate=params["learning_rate"],
    )

    result_str = "\t".join(["{}-{:.4f}".format(k, v) for k, v in eval_results.items()])

    key_info = [
        "dataset",
        "train_anomaly_ratio",
        "feature_type",
        "label_type",
        "use_attention",
    ]

    args_str = "\t".join(
        ["{}:{}".format(k, v) for k, v in params.items() if k in key_info]
    )

    dump_final_results(params, eval_results, model)
