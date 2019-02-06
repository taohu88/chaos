import math
import copy

import torch
from ignite.engine import Events
from ignite.metrics import Accuracy, Loss, RunningAverage
from ignite._utils import convert_tensor

from ..metrics._metric_enum import _MetricEnum
from ..metrics.snapshot import Snapshot
from ..metrics.lr_snapshot import LrSnapshot

from .callbacks._def_batch_printer import DefBatchPrinter
from .callbacks._def_evaluation_printer import DefEvaluationPrinter
from .callbacks.record_lr_loss import RecordLrAndLoss
from .callbacks.scheduler_listener import SchedulerListener
from .callbacks.metric_adapter import MetricAdapter
from .callbacks.run_evaluator import RunEvaluator

from .schedulers import create_lr_finder_scheduler, create_scheduler
from .engine import TeaEngine
from ..optimizer.adamw import AdamW


def _prepare_batch(batch, device=None, non_blocking=False):
    """Prepare batch for training: pass to a device with options
    """
    x, y = batch
    return (convert_tensor(x, device=device, non_blocking=non_blocking),
            convert_tensor(y, device=device, non_blocking=non_blocking))


# TODO fix this, not just use Adam
def create_optimizer(cfg, model, lr):
    momentum = cfg.get_momentum()
    weight_decay = cfg.get_weight_decay()
    optimizer = AdamW(model.parameters(), lr=lr, betas=(0.9, 0.99), weight_decay=weight_decay)
    return optimizer


def create_trainer(cfg, model, optimizer):
    device = cfg.get_device()
    loss_fn = cfg.get_loss_fn()

    if device:
        model.to(device)

    def _update(engine, batch):
        model.train()
        optimizer.zero_grad()
        x, y = _prepare_batch(batch, device=device, non_blocking=False)
        y_pred = model(x)
        loss = loss_fn(y_pred, y)
        loss.backward()
        optimizer.step()
        return y_pred, y, loss.item()

    return TeaEngine(_update)


def create_evaluator(cfg, model, metrics = {}):
    device = cfg.get_device()
    if device:
        model.to(device)

    def _inference(engine, batch):
        model.eval()
        with torch.no_grad():
            x, y = _prepare_batch(batch, device=device, non_blocking=False)
            y_pred = model(x)
            return y_pred, y

    engine = TeaEngine(_inference)

    for name, metric in metrics.items():
        metric.attach(engine, name)

    return engine


def build_trainer(cfg, model):
    return BaseLearner(cfg, model)


def find_lr(learner, train_dl, start_lr=1.0e-7, end_lr=10, batches=100, path='/tmp/lr_tmp.pch'):
    learner.save_model(path, with_optimizer=False)

    lr = learner.cfg.get_lr()
    optimizer = create_optimizer(learner.cfg, learner.model, lr)
    trainer = create_trainer(learner.cfg, learner.model, optimizer)
    scheduler = create_lr_finder_scheduler(optimizer, lr, start_lr, end_lr, batches)

    recorder = RecordLrAndLoss(scheduler, batches)
    recorder.attach(trainer)

    max_epochs = math.ceil(batches/len(train_dl))
    trainer.run(train_dl, max_epochs=max_epochs)
    learner.load_model(path)

    return recorder


def _attach_callbacks(engine, callbacks):
    for cb in callbacks:
        cb.attach(engine)


def _create_def_cbs(cfg, evaluator, train_dl, valid_dl, scheduler):
    cbs = []
    log_freq = cfg.get_log_freq()
    if log_freq > 0:
        log_train_loss = DefBatchPrinter(log_freq, len(train_dl))
        cbs.append(log_train_loss)

    if evaluator:
        cbs.append(RunEvaluator(evaluator, valid_dl))
        s_listener = SchedulerListener(scheduler, monitor_metric=_MetricEnum.valid_loss.value)
    else:
        s_listener = SchedulerListener(scheduler, monitor_metric=_MetricEnum.train_loss.value)
    cbs.append(s_listener)
    cbs.append(DefEvaluationPrinter())
    return cbs


def _create_def_metrics(cfg, trainer, evaluator, opt):
    cbs = []
    if evaluator:
        loss_fn = cfg.get_loss_fn()
        cbs.append(MetricAdapter(_MetricEnum.valid_loss.value, Loss(loss_fn)))
    else:
        cbs.append(MetricAdapter(_MetricEnum.batch_loss.value,
                                 Snapshot(output_transform=lambda x: x[2], when=Events.ITERATION_COMPLETED)))
        cbs.append(MetricAdapter(_MetricEnum.train_loss.value, RunningAverage(output_transform=lambda x: x[2])))
    cbs.append(MetricAdapter(_MetricEnum.lrs.value, LrSnapshot(trainer, opt)))
    return cbs


def _create_metrics_callbacks(cfg, trainer, evaluator, metrics, opt, use_def_print):
    cbs = []
    for name, m in metrics.items():
        cbs.append(MetricAdapter(name, m))

    if use_def_print:
        cbs += _create_def_metrics(cfg, trainer, evaluator, opt)
    _attach_callbacks(evaluator if evaluator else trainer, cbs)


def _create_call_backs(cfg, trainer, evaluator, train_dl, valid_dl, scheduler, callbacks, use_def_print):
    cbs = []
    if callbacks:
        for c in callbacks:
            cbs.append(c)

    if use_def_print:
        cbs += _create_def_cbs(cfg, evaluator, train_dl, valid_dl, scheduler)
    _attach_callbacks(trainer, cbs)


def fit(learner, train_dl, valid_dl=None,
        start_epoch=0,
        metrics={},
        callbacks=None,
        use_def_print=True):
    lr = learner.cfg.get_lr()
    optimizer = create_optimizer(learner.cfg, learner.model, lr)
    trainer = create_trainer(learner.cfg, learner.model, optimizer)
    scheduler = create_scheduler(learner.cfg, optimizer)
    evaluator = create_evaluator(learner.cfg, learner.model) if valid_dl else None

    _create_metrics_callbacks(learner.cfg, trainer, evaluator, metrics, optimizer, use_def_print)
    _create_call_backs(learner.cfg, trainer, evaluator, train_dl, valid_dl, scheduler, callbacks, use_def_print)

    max_epochs = learner.cfg.get_epochs()
    trainer.run(train_dl, start_epoch=start_epoch, max_epochs=max_epochs)

#
# TODO: what is the value of learner in this case
#
class BaseLearner(object):
    """
    This is just plain supervised learner(trainer/evalulator)
    """
    def __init__(self, cfg, model):
        self.cfg = cfg
        self.model = model

    def save_model(self, path, with_optimizer=False):
        if with_optimizer:
            state = {'model': self.model.state_dict(), 'opt':self.optimizer.state_dict()}
        else:
            state = {'model': self.model.state_dict()}
        torch.save(state, path)

    def load_model(self, path):
        device = self.cfg.get_device()
        state = torch.load(path, map_location=device)

        if 'model' in state:
            self.model.load_state_dict(state['model'])

        if 'opt' in state:
            self.optimizer.load_state_dict(state['opt'])

    def copy_model_state(self, with_optimizer=False):
        """
        Warning this will waste your gpu space, so it is not recommended
        :param with_optimizer:
        :return model_state and optimizer state

        """
        model_state = copy.deepcopy(self.model.state_dict())
        if with_optimizer:
            opt_state = copy.deepcopy(self.optimizer.state_dict())
        return model_state, opt_state

    def restore_model_state(self, model_state, opt_state=None):
        self.model.load_state_dict(model_state)
        if opt_state:
            self.optimizer.load_state_dict(opt_state)


BaseLearner.fit = fit
BaseLearner.find_lr = find_lr
