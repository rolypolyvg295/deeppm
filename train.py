# Copyright 2018 Dong-Hyun Lee, Kakao Brain.

""" Training Config & Helper Classes  """

import os
import json
from typing import NamedTuple
from tqdm.auto import tqdm

import torch
import torch.nn as nn
import torch.optim as optim
import torch.autograd as autograd
import time
import losses as ls
import random
from torch.utils.data import DataLoader

class Config(NamedTuple):
    """ Hyperparameters for training """
    seed: int = 3431 # random seed
    batch_size: int = 32
    lr: int = 5e-5 # learning rate
    n_epochs: int = 10 # the number of epoch
    # `warm up` period = warmup(0.1)*total_steps
    # linearly increasing learning rate from zero to the specified value(5e-5)
    warmup: float = 0.001
    #save_steps: int = 100 # interval for saving model
    #total_steps: int = 100000 # total number of steps to train

    @classmethod
    def from_json(cls, file): # load config from json file
        return cls(**json.load(open(file, "r")))

class LossReporter(object):
    def __init__(self, experiment, n_data_points):
        # type: (Experiment, int, tr.Train) -> None

        self.experiment = experiment
        self.n_datapoints = n_data_points
        self.start_time = time.time()

        self.loss = 1.0
        self.avg_loss = 1.0
        self.epoch_no = 0
        self.total_processed_items = 0
        self.epoch_processed_items = 0
        self.accuracy = 0.0

        self.last_report_time = 0.0
        self.last_save_time = 0.0

        self.root_path = self.experiment.experiment_root_path()

        try:
            os.makedirs(self.root_path)
        except OSError:
            pass

        self.loss_report_file = open(os.path.join(self.root_path, 'loss_report.log'), 'w',1)
        self.pbar = tqdm(desc = self.format_loss(), total=self.n_datapoints)

    def format_loss(self):

        return 'Epoch {}, Loss: {:.2}, {:.2}, Accuracy: {:.2}'.format(
                self.epoch_no,
                self.loss,
                self.avg_loss,
                self.accuracy
        )

    def start_epoch(self, epoch_no):
        
        self.epoch_no = epoch_no
        self.epoch_processed_items = 0
        self.accuracy = 0.0

        self.pbar.close()
        self.pbar = tqdm(desc=self.format_loss(), total=self.n_datapoints)

    def report(self, n_items, loss, avg_loss, t_accuracy):

        self.loss = loss
        self.avg_loss = avg_loss
        #self.accuracy = (self.accuracy * self.epoch_processed_items + t_accuracy * n_items) / (self.epoch_processed_items + n_items)
        self.accuracy = t_accuracy
        self.epoch_processed_items += n_items
        self.total_processed_items += n_items

        desc = self.format_loss()
        self.pbar.set_description(desc)
        self.pbar.update(n_items)

    def check_point(self, model, optimizer, lr_scheduler, file_name):

        state_dict = {
            'epoch': self.epoch_no,
            'model': model.state_dict(),
            'optimizer':optimizer.state_dict(),
            'lr_scheduler': lr_scheduler.state_dict(),
        }
            
        try: 
            os.makedirs(os.path.dirname(file_name))
        except OSError:
            pass

        torch.save(state_dict, file_name) 

    def end_epoch(self, model, optimizer, lr_scheduler, loss):
        
        self.loss = loss

        t = time.time()
        message = '\t'.join(map(str, (
            self.epoch_no,
            t - self.start_time,
            self.loss,
            self.accuracy,
        )))
        self.loss_report_file.write(message + '\n')

        file_name = os.path.join(self.experiment.checkpoint_file_dir(),'{}.mdl'.format(self.epoch_no))
        self.check_point(model,optimizer, lr_scheduler, file_name)


    def finish(self, model, optimizer, lr_scheduler):

        self.pbar.close()
        print("Finishing training")

        file_name = os.path.join(self.root_path, 'trained.mdl')
        self.check_point(model,optimizer, lr_scheduler, file_name)


class Trainer(object):
    """ Training Helper Class """
    def __init__(self, train_cfg, model, ds, expt, optimizer, lr_scheduler, loss_fn, device):
        self.train_cfg = train_cfg # config for training : see class Config
        self.model = model
        self.train_ds, self.test_ds = ds
        self.expt = expt
        self.optimizer = optimizer
        self.lr_scheduler = lr_scheduler
        self.save_dir = self.expt.experiment_root_path()
        self.device = device # device name

        self.loss_fn = loss_fn
        self.loss_reporter = LossReporter(expt, len(self.train_ds))

        self.tolerance = 25.

    def correct_regression(self, x, y):
        if x.shape != ():
            x = x[-1]
            y = y[-1]

        percentage = torch.abs(x - y) * 100.0 / (y + 1e-3)

        if percentage < self.tolerance:
            self.correct += 1

    def torch_correct_regression(self, x, raw_y):
        x = torch.exp(x) - 1e-4
        percentage = torch.abs(x - raw_y) * 100.0 / (raw_y + 1e-3)
        return sum(percentage < self.tolerance)


    def print_final(self, f, x, y):
        if x.shape != ():
            size = x.shape[0]
            for i in range(size):
                f.write('%f,%f ' % (x[i],y[i]))
            f.write('\n')
        else:
            f.write('%f,%f\n' % (x,y))

    def validate(self, resultfile):
        self.model.eval()
        self.model.to(self.device)

        f = open(resultfile,'w')

        correct = 0
        total_losses = []
        with torch.no_grad():
            loader = DataLoader(self.test_ds, shuffle=False, num_workers=2,
                        batch_size=self.train_cfg.batch_size, collate_fn=self.test_ds.block_collate_fn)
            for x, target, target_raw in tqdm(loader):
                x = x.to(self.device)
                target = target.to(self.device)
                target_raw = target_raw.to(self.device)

                # print(x.shape)
                output = self.model(x)
                correct += self.torch_correct_regression(output, target_raw)

                loss = self.loss_fn(output, target)
                total_losses.append(loss)

        ret_loss = sum(total_losses)/len(total_losses)
        f.write(f'loss - {ret_loss}\n')
        f.write(f'{correct}, {len(self.test_ds)}\n')
        print(f'Validate: loss - {sum(total_losses)/len(total_losses)}\n\t{correct}/{len(self.test_ds)} = {correct/len(self.test_ds)}\n')
        print()
        f.close()
        return ret_loss

    def train(self):
        """ Train Loop """
        resultfile = os.path.join(self.expt.experiment_root_path(), 'validation_results.txt')

        self.model.to(self.device)

        loader = DataLoader(self.train_ds, shuffle=True, num_workers=2, 
                        batch_size=self.train_cfg.batch_size, collate_fn=self.train_ds.block_collate_fn)

        # with autograd.detect_anomaly(False):
        for epoch_no in range(self.train_cfg.n_epochs):
            epoch_loss_sum = 0.
            step = 0
            total_correct = 0
            total_cnts = 0
            print(f'using lr: {self.optimizer.param_groups[0]["lr"]}')
            self.loss_reporter.start_epoch(epoch_no + 1) 

            self.model.train()
            for idx, (x, y, raw_y) in enumerate(loader):
                self.model.train()
                self.optimizer.zero_grad()
                x = x.to(self.device)
                y = y.to(self.device)
                raw_y = raw_y.to(self.device)

                output = self.model(x)
                loss = self.loss_fn(output, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), .2)

                
                for param in self.model.parameters():
                    if param.grad is None:
                        continue

                    if torch.isnan(param.grad).any():
                        print("BAD: isnan found in grad")
                        self.loss_reporter.finish(self.model, self.optimizer, self.lr_scheduler)
                        return
                
                self.optimizer.step()

            
                step += 1
                epoch_loss_sum += loss.item()
                total_correct += self.torch_correct_regression(output, raw_y)
                total_cnts += len(y)
                self.loss_reporter.report(len(y), loss.item(), epoch_loss_sum/step, total_correct/total_cnts)   

            epoch_loss_avg = epoch_loss_sum / step
            self.loss_reporter.end_epoch(self.model,self.optimizer, self.lr_scheduler, epoch_loss_avg)

            val_loss = self.validate(resultfile)
            self.lr_scheduler.step(val_loss)
        self.loss_reporter.finish(self.model,self.optimizer, self.lr_scheduler)
