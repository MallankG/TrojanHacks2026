"""GATv2 "IMPROVED" edge classifier on IBM AML HI-Small, after Shahbazi et al. (2026), arXiv:2609.37155.

IMPROVED = GATv2 + bidirectional message passing (separate forward / reverse convs)
+ per-layer edge updates + port-aware edge features. Paper test: AUPRC 64.85, Best F1 68.42.

Same edges and temporal split as ml/ibm/xgb.py (paper cutoffs). Split graphs follow the usual
temporal protocol: training sees only train edges; validation scores val edges on the train+val
graph; test scores test edges on the full graph. No edge from a later split is ever visible
(within a split window the graph is static, as in the Multi-GNN / paper protocol).
Mini-batches are 2-hop neighbourhoods sampled on the GPU (no pyg-lib / torch-sparse needed).

    python -m ml.ibm.gnn --data <dir with HI-Small_Trans.csv + HI-Small_accounts.csv> --out ml/artifacts/ibm
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score
from torch import nn
from torch_geometric.nn import GATv2Conv

from .xgb import ENTITY_TYPES, PAPER_CUTOFFS, edge_features, load, metrics, split

PAPER_GNN = {"auprc": 64.85, "best_f1": 68.42}
AMP = torch.cuda.is_available() and torch.cuda.is_bf16_supported()  # bf16 on A100/L4; fp32 on T4


# --- features -------------------------------------------------------------
def gnn_edge_features(e):
    """11 paper edge features + ports + inter-transaction times (all depend only on the past)."""
    ef = edge_features(e)
    ef["log_amt_recv"] = np.log1p(e["amt_recv"])
    order = np.argsort(e["ts"].to_numpy(), kind="stable")
    s = e.iloc[order][["src", "dst", "ts"]]
    first = s.drop_duplicates(["src", "dst"])
    out_port = first.groupby("src").cumcount().rename("out_port")
    in_port = first.drop_duplicates(["dst", "src"]).groupby("dst").cumcount().rename("in_port")
    s = s.join(out_port).join(in_port)
    s[["out_port", "in_port"]] = s.groupby(["src", "dst"])[["out_port", "in_port"]].ffill()
    s["dt_src"] = s.groupby("src")["ts"].diff().dt.total_seconds()
    s["dt_dst"] = s.groupby("dst")["ts"].diff().dt.total_seconds()
    s = s.sort_index()
    for c in ("out_port", "in_port"):
        ef[c] = np.log1p(s[c].to_numpy(float))
    for c in ("dt_src", "dt_dst"):
        ef[c] = np.log1p(s[c].fillna(1e7).to_numpy(float) / 60)
    return ef


class SplitGraph:
    """Edges [0, n_visible) of the time-sorted edge list, with an incident-edge CSR on the device."""

    def __init__(self, src, dst, n_nodes, visible, device):
        s, d = src[visible], dst[visible]
        eid = torch.as_tensor(visible, device=device)
        node = torch.cat([s, d])
        inc = torch.cat([eid, eid])
        order = torch.argsort(node, stable=True)
        self.inc = inc[order]
        self.rowptr = torch.zeros(n_nodes + 1, dtype=torch.long, device=device)
        self.rowptr[1:] = torch.cumsum(torch.bincount(node, minlength=n_nodes), 0)
        deg_in = torch.bincount(d, minlength=n_nodes).float()
        deg_out = torch.bincount(s, minlength=n_nodes).float()
        self.deg = torch.stack([torch.log1p(deg_in), torch.log1p(deg_out)], 1) / 5

    def sample(self, nodes, k, gen):
        start, deg = self.rowptr[nodes], self.rowptr[nodes + 1] - self.rowptr[nodes]
        has = deg > 0
        start, deg = start[has], deg[has]
        off = (torch.rand(len(deg), k, device=deg.device, generator=gen) * deg[:, None]).long()
        return torch.unique(self.inc[(start[:, None] + off).flatten()])


class EdgeGATv2(nn.Module):
    def __init__(self, node_dim, edge_dim, hidden=64, heads=4, layers=2, dropout=0.1):
        super().__init__()
        self.config = dict(node_dim=node_dim, edge_dim=edge_dim, hidden=hidden, heads=heads, layers=layers, dropout=dropout)
        self.node_in = nn.Linear(node_dim, hidden)
        self.edge_in = nn.Linear(edge_dim, hidden)
        conv = lambda: GATv2Conv(hidden, hidden // heads, heads=heads, edge_dim=hidden, add_self_loops=False)
        self.fwd = nn.ModuleList([conv() for _ in range(layers)])
        self.rev = nn.ModuleList([conv() for _ in range(layers)])
        self.mix = nn.ModuleList([nn.Linear(2 * hidden, hidden) for _ in range(layers)])
        self.edge_upd = nn.ModuleList([nn.Sequential(nn.Linear(3 * hidden, hidden), nn.ReLU(), nn.Linear(hidden, hidden)) for _ in range(layers)])
        self.n_norm = nn.ModuleList([nn.LayerNorm(hidden) for _ in range(layers)])
        self.e_norm = nn.ModuleList([nn.LayerNorm(hidden) for _ in range(layers)])
        self.head = nn.Sequential(nn.Linear(3 * hidden, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, ei, ea, target):
        h, e = torch.relu(self.node_in(x)), torch.relu(self.edge_in(ea))
        rev = ei.flip(0)
        for f, r, m, u, nn_, en in zip(self.fwd, self.rev, self.mix, self.edge_upd, self.n_norm, self.e_norm):
            h = nn_(h + self.drop(torch.relu(m(torch.cat([f(h, ei, e), r(h, rev, e)], 1)))))
            e = en(e + self.drop(u(torch.cat([h[ei[0]], h[ei[1]], e], 1))))
        t = target
        return self.head(torch.cat([h[ei[0, t]], h[ei[1, t]], e[t]], 1)).squeeze(1)


class Data:
    """All edges on the device; batches = seed edges + sampled 2-hop neighbourhood in a split graph."""

    def __init__(self, src, dst, node_x, edge_x, device, fanout=(20, 8), ego=False):
        self.src, self.dst = torch.as_tensor(src, device=device), torch.as_tensor(dst, device=device)
        self.node_x, self.edge_x = torch.as_tensor(node_x, device=device), torch.as_tensor(edge_x, device=device)
        self.n_nodes, self.device, self.fanout, self.ego = len(node_x), device, fanout, ego

    def batch(self, g, seeds, gen):
        nodes = torch.unique(torch.cat([self.src[seeds], self.dst[seeds]]))
        edges = [seeds]
        for k in self.fanout:
            new = g.sample(nodes, k, gen)
            edges.append(new)
            nodes = torch.unique(torch.cat([nodes, self.src[new], self.dst[new]]))
        eids, inv = torch.unique(torch.cat(edges), return_inverse=True)
        target = inv[: len(seeds)]
        nid, local = torch.unique(torch.cat([self.src[eids], self.dst[eids]]), return_inverse=True)
        ei = local.view(2, -1)
        x = torch.cat([self.node_x[nid], g.deg[nid]], 1)
        if self.ego:  # Multi-GNN ego IDs: flag the endpoints of the edges being classified
            flag = torch.zeros(len(nid), 1, device=x.device)
            flag[ei[:, target].flatten()] = 1
            x = torch.cat([x, flag], 1)
        return x, ei, self.edge_x[eids], target


@torch.no_grad()
def predict(model, data, g, rows, bs=4096, seed=0):
    model.eval()
    gen = torch.Generator(device=data.device).manual_seed(seed)
    out = []
    for i in range(0, len(rows), bs):
        seeds = torch.as_tensor(rows[i:i + bs], device=data.device)
        with torch.autocast(data.device.type, dtype=torch.bfloat16, enabled=AMP):
            out.append(torch.sigmoid(model(*data.batch(g, seeds, gen)).float()).cpu())
    return torch.cat(out).numpy()


def main(data_dir, out=None, epochs=30, patience=5, bs=2048, neg_frac=0.25, seed=7, hidden=64, lr=1e-3, pos_weight=5.0,
         fanout=(20, 8), ego=False, tag="gnn"):
    torch.manual_seed(seed)
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    tx, acc = load(data_dir)
    e = tx[tx["src"] != tx["dst"]].sort_values("ts", kind="stable").reset_index(drop=True)
    y = e["y"].to_numpy()
    tr, va, te, cut = split(e["ts"])
    assert tr.max() < va.min() and va.max() < te.min(), "edges must be time-sorted so split graphs are prefixes"
    print(f"edges {len(e):,} | train {len(tr):,} ({y[tr].sum()}) val {len(va):,} ({y[va].sum()}) test {len(te):,} ({y[te].sum()}) | {dev}")

    ids = pd.Index(pd.unique(np.r_[acc["Account Number"].to_numpy(), e["src"].to_numpy(), e["dst"].to_numpy()]))
    ent = acc.set_index("Account Number")["Entity Name"].str.replace(r" #.*", "", regex=True).reindex(ids)
    node_x = np.column_stack([(ent == t).to_numpy(float) for t in ENTITY_TYPES]).astype(np.float32)
    ef = gnn_edge_features(e)
    mu, sd = ef.iloc[tr].mean(), ef.iloc[tr].std() + 1e-6
    edge_x = ((ef - mu) / sd).to_numpy(np.float32)
    src, dst = ids.get_indexer(e["src"]), ids.get_indexer(e["dst"])
    print(f"features ready in {time.time() - t0:.0f}s: node {node_x.shape}, edge {edge_x.shape}")

    D = Data(src, dst, node_x, edge_x, dev, tuple(fanout), ego)
    graphs = {k: SplitGraph(D.src, D.dst, D.n_nodes, np.arange(last + 1), dev) for k, last in (("train", tr.max()), ("val", va.max()), ("test", te.max()))}
    model = EdgeGATv2(node_x.shape[1] + 2 + int(ego), edge_x.shape[1], hidden=hidden).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    lossf = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight, device=dev))
    gen = torch.Generator(device=dev).manual_seed(seed)
    rng = np.random.default_rng(seed)
    pos, neg = tr[y[tr] == 1], tr[y[tr] == 0]
    yt = torch.as_tensor(y, device=dev, dtype=torch.float32)
    best, best_state, bad, history = -1.0, None, 0, []
    for ep in range(epochs):
        model.train()
        t1 = time.time()
        rows = rng.permutation(np.r_[pos, rng.choice(neg, int(neg_frac * len(neg)), replace=False)])
        tot = 0.0
        for i in range(0, len(rows), bs):
            seeds = torch.as_tensor(rows[i:i + bs], device=dev)
            with torch.autocast(dev.type, dtype=torch.bfloat16, enabled=AMP):
                logit = model(*D.batch(graphs["train"], seeds, gen)).float()
            loss = lossf(logit, yt[seeds])
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item()
        pv = predict(model, D, graphs["val"], va)
        ap = 100 * average_precision_score(y[va], pv)
        history.append({"epoch": ep + 1, "loss": tot / (len(rows) / bs), "val_auprc": ap})
        print(f"epoch {ep + 1:2d} loss {history[-1]['loss']:.4f} val AUPRC {ap:.2f} ({time.time() - t1:.0f}s)", flush=True)
        if ap > best:
            best, bad, best_state = ap, 0, {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    pv, pt = predict(model, D, graphs["val"], va), predict(model, D, graphs["test"], te)
    res = {"val": metrics(y[va], pv), "test": metrics(y[te], pt)}
    thr = res["val"]["best_threshold"]
    pred = pt >= thr
    tp, fp, fn = int((pred & (y[te] == 1)).sum()), int((pred & (y[te] == 0)).sum()), int((~pred & (y[te] == 1)).sum())
    res["test_at_val_threshold"] = {"threshold": thr, "precision": 100 * tp / max(tp + fp, 1), "recall": 100 * tp / max(tp + fn, 1),
                                    "f1": 100 * 2 * tp / max(2 * tp + fp + fn, 1), "tp": tp, "fp": fp, "fn": fn}
    print("\nTEST " + "  ".join(f"{k} {v:.2f}" for k, v in res["test"].items()))
    print(f"paper IMPROVED: AUPRC {PAPER_GNN['auprc']}  Best F1 {PAPER_GNN['best_f1']}  -> AUPRC gap {res['test']['auprc'] - PAPER_GNN['auprc']:+.2f}")
    print("test @ val threshold:", res["test_at_val_threshold"])
    if out:
        o = Path(out)
        o.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": {k: v.cpu() for k, v in model.state_dict().items()}, "config": model.config,
                    "edge_mu": mu.to_dict(), "edge_sd": sd.to_dict(), "edge_features": list(ef.columns),
                    "fanout": D.fanout, "ego": ego, "entity_types": ENTITY_TYPES}, o / f"ibm_{tag}.pt")
        np.savez_compressed(o / f"ibm_{tag}_scores.npz", y_val=y[va], s_val=pv, y_test=y[te], s_test=pt)  # for conformal.py
        (o / f"ibm_{tag}_results.json").write_text(json.dumps(
            {"paper": PAPER_GNN, "split_cutoffs": PAPER_CUTOFFS, "results": res, "history": history,
             "hparams": dict(epochs=epochs, bs=bs, neg_frac=neg_frac, seed=seed, hidden=hidden, lr=lr, pos_weight=pos_weight, fanout=list(fanout), ego=ego),
             "operating_threshold": thr}, indent=2))
    return res


class IBMGNNScorer:
    """Serve the trained GNN. `edges` must hold the history the target rows are judged against.

        scorer = IBMGNNScorer()
        scorer.score(edges, accounts, rows=[...])  # edges: ts, src, dst, amt_paid, amt_recv, cur_paid, cur_recv, fmt
    """

    def __init__(self, artifact_dir=Path(__file__).parent.parent / "artifacts" / "ibm", device="cpu", name="gnn"):
        d = Path(artifact_dir)
        ck = torch.load(d / f"ibm_{name}.pt", map_location=device, weights_only=False)
        self.ck, self.device = ck, torch.device(device)
        self.model = EdgeGATv2(**ck["config"]).to(self.device).eval()
        self.model.load_state_dict(ck["state_dict"])
        self.threshold = json.loads((d / f"ibm_{name}_results.json").read_text())["operating_threshold"]

    def score(self, edges, accounts, rows=None):
        e = edges.assign(ts=pd.to_datetime(edges["ts"])).sort_values("ts", kind="stable")
        orig = e.index.to_numpy()
        e = e.reset_index(drop=True)
        rows = np.arange(len(e)) if rows is None else np.flatnonzero(np.isin(orig, rows))
        ids = pd.Index(pd.unique(np.r_[accounts["Account Number"].to_numpy(), e["src"].to_numpy(), e["dst"].to_numpy()]))
        ent = accounts.drop_duplicates("Account Number").set_index("Account Number")["Entity Name"].str.replace(r" #.*", "", regex=True).reindex(ids)
        node_x = np.column_stack([(ent == t).to_numpy(float) for t in self.ck["entity_types"]]).astype(np.float32)
        ef = gnn_edge_features(e)[self.ck["edge_features"]]
        edge_x = ((ef - pd.Series(self.ck["edge_mu"])) / pd.Series(self.ck["edge_sd"])).to_numpy(np.float32)
        D = Data(ids.get_indexer(e["src"]), ids.get_indexer(e["dst"]), node_x, edge_x, self.device, tuple(self.ck["fanout"]), self.ck.get("ego", False))
        g = SplitGraph(D.src, D.dst, D.n_nodes, np.arange(len(e)), self.device)
        p = predict(self.model, D, g, rows)
        return pd.DataFrame({"row": orig[rows], "gnn_score": p, "gnn_flagged": p >= self.threshold}).set_index("row")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--pos-weight", type=float, default=5.0)
    ap.add_argument("--neg-frac", type=float, default=0.25)
    ap.add_argument("--bs", type=int, default=2048)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--fanout", type=int, nargs="+", default=[20, 8])
    ap.add_argument("--ego", action="store_true")
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--tag", default="gnn", help="artifact name: ibm_<tag>.pt / _results.json / _scores.npz")
    a = ap.parse_args()
    main(a.data, a.out, epochs=a.epochs, patience=a.patience, bs=a.bs, neg_frac=a.neg_frac, hidden=a.hidden, lr=a.lr,
         pos_weight=a.pos_weight, fanout=a.fanout, ego=a.ego, tag=a.tag)
