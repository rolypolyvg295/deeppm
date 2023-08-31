import torch
import torch.nn as nn
from utils import get_device
from losses import load_losses

from .base_class import BaseModule
from .pos_encoder import get_positional_encoding_1d, get_positional_encoding_2d
from .base import Seq, Op, BasicBlock


class BertB6(BaseModule):
    def __init__(self, pretrained=None, pad_idx=0, 
                 n_heads=12,
                loss_type='MapeLoss', loss_fn_arg={}, pred_drop=0.1):
        super().__init__()

        self.pt_embed = pretrained.embed
        self.pt_pos2d_embed = pretrained.pos2d_embed
        self.pt_mixed = pretrained.mixed

        dim = pretrained.embed.embedding_dim
        dim_ff = 4 * dim
        self.pad_idx = pad_idx


        self.pos_embed = get_positional_encoding_1d(dim)
        self.single = Seq(dim, dim_ff, n_heads, 2)
        self.mixed = BasicBlock(dim, dim_ff, n_heads, 2)

        self.t_single = Seq(dim, dim_ff, n_heads, 1)
        self.t_mixed = BasicBlock(dim, dim_ff, n_heads, 1)
        self.t_op = Op(dim, dim_ff, n_heads, 1)

        # self.prediction = nn.Sequential(
        #     nn.Linear(dim, dim),
        #     nn.ReLU(),
        #     nn.Dropout(pred_drop),
        #     nn.Linear(dim, 1)
        # )

        self.prediction = nn.Sequential(
            nn.Dropout(pred_drop),
            nn.Linear(dim, 1)
        )

        self.merger = nn.Sequential(
            nn.Dropout(pred_drop),
            nn.Linear(3 * dim, dim)
        )

        self.loss = load_losses(loss_type, loss_fn_arg)
        
    def forward(self, x):
        batch_size, inst_size, seq_size = x.shape
        mask = x == self.pad_idx
        op_seq_mask = mask.all(dim=-1)

        output = self.pt_embed(x)
        output = self.pt_pos2d_embed(output)

        # Mixed = B I S D
        output = self.pt_mixed(output, mask)
        output = output.masked_fill(mask.unsqueeze(-1), 0)

        
        # Mixed
        mixed_output = self.mixed(output, mask)
        single_out = self.single(output, mask, op_seq_mask)
    
        t_out = self.t_single(output, mask, op_seq_mask)
        t_out = self.t_mixed(t_out, mask)
        t_out = t_out.sum(dim=2)
        t_out = self.pos_embed(t_out)
        t_out = self.t_op(t_out, op_seq_mask)

        #  Merging
        single_out = single_out.sum(dim=2)
        mixed_output = mixed_output.sum(dim=2)
        op_seq_mask = op_seq_mask.view(batch_size, inst_size)

        output = torch.stack((single_out, mixed_output, t_out), dim=2)
        output = output.view(batch_size, inst_size, -1)
        output = self.merger(output)
        output = self.prediction(output)
        output = output.masked_fill(op_seq_mask.unsqueeze(-1), 0)
        output = output.squeeze(-1)
        output = output.sum(dim = 1)

        return output
    
    def get_loss(self):
        return self.loss
    