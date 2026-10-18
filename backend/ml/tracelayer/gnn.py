"""Time-respecting GATv2 edge classifier (hybrid graph + tabular).

Transactions are scored in 6-hour buckets. For a bucket starting at b, the
message-passing graph contains only transactions in [b - 14d, b): no edge from
the bucket itself or the future is visible, so there is no future-neighbour
leakage. Both payment directions are kept as separate edge types (direction
bit), following the payment/receipt split motivated by LineMVGNN. The head sees
source/destination embeddings plus the target transaction's own leakage-safe
tabular features (as in hybrid graph+tabular AML frameworks).
"""
import numpy as np
import torch
from torch import nn
from torch_geometric.nn import GATv2Conv

from .features import ACCOUNT_TYPES, CHANNELS, _secs

CONTEXT_SECS = 14 * 86400
BUCKET_SECS = 6 * 3600
NODE_DIM = 1 + len(ACCOUNT_TYPES) + 4
EDGE_DIM = 1 + len(CHANNELS) + 2


class TxGraph:
    """All known transactions as time-sorted edges plus static account attributes."""

    def __init__(self, tx, accounts):
        ids = {a: i for i, a in enumerate(accounts["account_id"])}
        self.n_nodes = len(ids)
        self.t = _secs(tx["timestamp"])
        assert np.all(np.diff(self.t) >= 0), "tx must be sorted by timestamp"
        self.src = tx["origin_account_id"].map(ids).to_numpy()
        self.dst = tx["destination_account_id"].map(ids).to_numpy()
        ch = tx["channel"].to_numpy()
        self.base = np.column_stack([(np.log1p(tx["amount"].to_numpy(float)) - 6) / 3] + [(ch == c) for c in CHANNELS]).astype(np.float32)
        acc_type = accounts["account_type"].to_numpy()
        self.type_oh = np.column_stack([(acc_type == c) for c in ACCOUNT_TYPES]).astype(np.float32)
        self.open_s = _secs(accounts["open_date"])
        self.bucket = self.t // BUCKET_SECS

    def snapshot(self, bucket_id):
        b = bucket_id * BUCKET_SECS
        lo, hi = np.searchsorted(self.t, [b - CONTEXT_SECS, b])
        s, d = self.src[lo:hi], self.dst[lo:hi]
        rec = ((b - self.t[lo:hi]) / CONTEXT_SECS).astype(np.float32)[:, None]
        base = self.base[lo:hi]
        ei = np.r_[np.c_[s, d], np.c_[d, s]].T
        ea = np.r_[np.c_[base, np.zeros_like(rec), rec], np.c_[base, np.ones_like(rec), rec]]
        amt = np.expm1(base[:, 0] * 3 + 6)
        n = self.n_nodes
        deg = [np.bincount(d, minlength=n), np.bincount(s, minlength=n),
               np.bincount(d, amt, minlength=n), np.bincount(s, amt, minlength=n)]
        age = np.log1p(np.clip((b - self.open_s) / 86400, 0, None))[:, None] / 8
        x = np.c_[age, self.type_oh, np.log1p(np.column_stack(deg)) / 5].astype(np.float32)
        return torch.from_numpy(x), torch.from_numpy(ei.astype(np.int64)), torch.from_numpy(ea)

    def targets(self, bucket_id):
        return np.flatnonzero(self.bucket == bucket_id)


class EdgeGAT(nn.Module):
    def __init__(self, tab_dim, hidden=64, heads=4, layers=2, dropout=0.1):
        super().__init__()
        self.config = dict(tab_dim=tab_dim, hidden=hidden, heads=heads, layers=layers, dropout=dropout)
        self.node_in = nn.Linear(NODE_DIM, hidden)
        self.convs = nn.ModuleList([GATv2Conv(hidden, hidden // heads, heads=heads, edge_dim=EDGE_DIM) for _ in range(layers)])
        self.norms = nn.ModuleList([nn.LayerNorm(hidden) for _ in range(layers)])
        self.tab = nn.Sequential(nn.Linear(tab_dim, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, hidden))
        self.head = nn.Sequential(nn.Linear(3 * hidden, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))
        self.drop = nn.Dropout(dropout)

    def embed(self, x, ei, ea):
        h = torch.relu(self.node_in(x))
        for conv, norm in zip(self.convs, self.norms):
            h = norm(h + self.drop(torch.relu(conv(h, ei, ea))))
        return h

    def forward(self, x, ei, ea, src, dst, tab):
        h = self.embed(x, ei, ea)
        return self.head(torch.cat([h[src], h[dst], torch.relu(self.tab(tab))], 1)).squeeze(1)


def tab_transform(f, stats):
    v = f[stats["columns"]].to_numpy(np.float32)
    v = np.sign(v) * np.log1p(np.abs(v))
    return ((v - stats["mean"]) / stats["std"]).astype(np.float32)


def fit_tab_stats(f, columns):
    v = f[columns].to_numpy(np.float32)
    v = np.sign(v) * np.log1p(np.abs(v))
    return {"columns": list(columns), "mean": v.mean(0), "std": v.std(0) + 1e-6}


@torch.no_grad()
def score(model, graph, tab, rows):
    """Probabilities for transaction rows (indices into graph/tab), bucket by bucket."""
    model.eval()
    out = np.zeros(len(rows), np.float32)
    rows = np.asarray(rows)
    for b in np.unique(graph.bucket[rows]):
        sel = np.flatnonzero(graph.bucket[rows] == b)
        r = rows[sel]
        x, ei, ea = graph.snapshot(b)
        logit = model(x, ei, ea, torch.from_numpy(graph.src[r]), torch.from_numpy(graph.dst[r]), torch.from_numpy(tab[r]))
        out[sel] = torch.sigmoid(logit).numpy()
    return out
