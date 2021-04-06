import json

import torch
import torch.nn as nn
from torch.nn import Conv1d, MaxPool1d, Linear, Dropout
import torch.nn.functional as F

from torch_geometric.nn import GCNConv, global_sort_pool
from torch_geometric.utils import remove_self_loops

from helpers import get_tactic_targets, get_true_tactics, get_true_args, get_pred_tactics, prep_asts, get_lc_targets, get_pred_lc

class GASTLCModel(nn.Module):
    def __init__(self, opts):
        super(GASTLCModel, self).__init__()
        self.opts = opts
        self.nonterminals = json.load(open(self.opts.nonterminals))
        self.tactics = json.load(open(self.opts.tactics))

        self.conv1 = GCNConv(len(self.nonterminals), self.opts.embedding_dim)
        self.conv2 = GCNConv(self.opts.embedding_dim, self.opts.embedding_dim)
        self.conv3 = GCNConv(self.opts.embedding_dim, self.opts.embedding_dim)
        self.conv4 = GCNConv(self.opts.embedding_dim, 1)
        self.conv5 = Conv1d(1, self.opts.embedding_dim//2, 3*self.opts.embedding_dim+1, 3*self.opts.embedding_dim+1)
        self.conv6 = Conv1d(self.opts.embedding_dim//2, self.opts.embedding_dim, 5, 1)
        self.pool = MaxPool1d(2, 2)
        dense_dim = int((self.opts.sortk - 2) / 2 + 1)
        self.dense_dim = (dense_dim - 5 + 1) * self.opts.embedding_dim
        self.classifier_1 = Linear(11*self.dense_dim, 128)
        self.drop_out = Dropout(self.opts.dropout)
        self.classifier_2 = Linear(128, 10)
        self.relu = nn.ReLU(inplace=True)
        self.tanh = nn.Tanh()
            
        self.criterion = nn.CrossEntropyLoss().to(self.opts.device)
        self.softmax = nn.Softmax(dim=-1)
        

    def forward(self, batch):
        goal_asts = [g["ast"] for g in batch["goal"]]
        x_goal, edge_index_goal, gnn_batch = prep_asts(self.opts, goal_asts, len(goal_asts))
        edge_index_goal, _ = remove_self_loops(edge_index_goal)
        edge_index_goal.to(self.opts.device)
        goal_embeddings = self.embeddings(x_goal, edge_index_goal, gnn_batch)

        lc_asts = [[c["ast"] for c in lc] for lc in batch["local_context"]]
        lc_asts = lc_asts[0]
        x_lc, edge_index_lc, gnn_batch = prep_asts(self.opts, lc_asts, len(goal_asts)*10)
        edge_index_lc, _ = remove_self_loops(edge_index_lc)
        edge_index_lc.to(self.opts.device)
        lc_embeddings = self.embeddings(x_lc, edge_index_lc, gnn_batch)
        
        embeddings = torch.cat((goal_embeddings, lc_embeddings))
        embeddings = torch.flatten(embeddings)

        out = self.relu(self.classifier_1(embeddings))
        out = self.drop_out(out)
        logits = self.classifier_2(out)
        logits = logits.view(-1, len(logits))

        targets, trues = get_lc_targets(self.opts, batch)

        loss = self.criterion(logits, targets)

        probs = self.softmax(logits)

        preds = get_pred_lc(self.opts, batch, probs)
        return preds, trues, loss

    def embeddings(self, x, edge_index, batch):
        x_1 = self.tanh(self.conv1(x, edge_index))
        x_2 = self.tanh(self.conv2(x_1, edge_index))
        x_3 = self.tanh(self.conv3(x_2, edge_index))
        x_4 = self.tanh(self.conv4(x_3, edge_index))
        x = torch.cat([x_1, x_2, x_3, x_4], dim=-1)
        x = global_sort_pool(x, batch, k=self.opts.sortk)
        x = x.view(x.size(0), 1, x.size(-1))
        x = self.relu(self.conv5(x))
        x = self.pool(x)
        x = self.relu(self.conv6(x))
        x = x.view(x.size(0), -1)
        return x
