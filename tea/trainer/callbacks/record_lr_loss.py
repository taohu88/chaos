import math
from .callback_src_enum import CallbackSrcEnum
from ignite.metrics import RunningAverage
from ignite.engine import Events
from tqdm import tqdm

from tea.utils.commons import self_or_first
from .callback import Callback


class RecordLrAndLoss(Callback):

    def __init__(self, scheduler, batches, listen_to=CallbackSrcEnum.train):
        super().__init__(listen_to=listen_to)
        self.scheduler = scheduler
        self.batches = batches
        self.lr_losses = []
        self.best_loss = None
        self.desc = "Running loss: {:.3f}"

        self.pbar = tqdm(
            initial=0, leave=False, total=batches,
            desc=self.desc.format(0)
        )

    def events_to_attach(self):
        return [Events.ITERATION_COMPLETED, Events.COMPLETED]

    def attach(self, engine):
        alpha = 0.10
        avg_output = RunningAverage(output_transform=lambda x: x[2], alpha=alpha)
        avg_output.attach(engine, 'running_avg_loss')
        super().attach(engine)

    def iteration_completed(self, engine):
        iter = engine.state.iteration

        if iter >= self.batches:
            self.stop(engine)
            return

        metrics = engine.state.metrics
        if 'running_avg_loss' not in metrics:
            return

        avg_loss = metrics['running_avg_loss']
        if math.isnan(avg_loss):
            self.stop(engine)
            return

        pbar = self.pbar
        if pbar:
            pbar.desc = self.desc.format(avg_loss)
            pbar.update(1)
        else:
            print(self.desc.format(avg_loss))

        if not self.best_loss:
            self.best_loss = avg_loss
        elif self.best_loss > avg_loss:
            self.best_loss = avg_loss

        if avg_loss > 4 * self.best_loss:
            self.stop(engine)
            return

        lrs = self.scheduler.get_lr()
        self.lr_losses.append((self_or_first(lrs), avg_loss))
        #step to the next level
        self.scheduler.step()

    def completed(self, engine):
        self.pbar.close()

    def get_lr_with_min_loss(self):
        return min(self.lr_losses, key = lambda t: t[1])

    def stop(self, engine):
        engine.terminate()


