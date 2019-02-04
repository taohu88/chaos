import os
import torch

from .cfg_enum import CfgEnum
from .parser import parse_config
from .loss_fn_map import get_loss_fn_maps



# def merge_to_section(self, section, a_dict):
#     for k, v in a_dict.items():
#         self.set(section, str(k), str(v))


def get_int(cfg, key, fallback=0):
    return int(cfg.get(key, fallback))


def is_true(cfg, key):
    return get_int(cfg, key) > 0


class AppConfig:

    def __init__(self, conf):
        self.conf = conf

    @classmethod
    def from_file(cls, path, **kwargs):
        conf = parse_config(path)
        conf.update(kwargs)
        return cls(conf)
    
    def print(self):
        print("Configurations:")
        for (k, v) in self.conf.items():
            print(f"\t{k} = {v}")

    def get_data_in_dir(self):
        return self.conf.get(CfgEnum.data_in_dir.value)

    def get_model_cfg(self):
        return self.conf.get(CfgEnum.model_cfg.value)

    def get_model_out_dir(self, create_no_exists=True):
        model_out_dir = self.conf.get(CfgEnum.model_out_dir.value)
        if create_no_exists and (not os.path.exists(model_out_dir)):
            os.makedirs(model_out_dir)
        return model_out_dir

    def get_epochs(self):
        return int(self.conf[CfgEnum.epochs.value])

    def get_device(self):
        use_cuda = get_int(self.conf, CfgEnum.use_gpu.value, fallback=0)
        if use_cuda and torch.cuda.is_available():
            return "cuda"
        return None

    def get_loss_fn(self):
        loss_name = self.conf[CfgEnum.loss.value]
        return get_loss_fn_maps()[loss_name]

    def get_lr(self):
        return float(self.conf[CfgEnum.lr.value])

    def get_momentum(self):
        return float(self.conf.get(CfgEnum.momentum.value, 0.0))

    def get_weight_decay(self):
        return float(self.conf.get(CfgEnum.weight_decay.value, 0.0))
    
    def get_log_freq(self):
        return get_int(self.conf, CfgEnum.log_freq.value, fallback=-1)

    def get_batch_sz(self):
        return int(self.conf[CfgEnum.batch_sz.value])

    def get_train_batch_sz(self):
        return int(self.conf.get(CfgEnum.train_batch_sz.value, self.get_batch_sz()))
    
    def get_val_batch_sz(self):
        return int(self.conf.get(CfgEnum.val_batch_sz.value, self.get_batch_sz()))

    def get_test_batch_sz(self):
        return int(self.conf.get(CfgEnum.test_batch_sz.value, self.get_batch_sz()))
    
    def get_num_workers(self):
        return get_int(self.conf, CfgEnum.num_workers.value, fallback=1)