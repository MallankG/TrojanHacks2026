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

from .order import canonical, canonical_order
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


_U = np.uint64
_GOLD, _M1, _M2 = _U(0x9E3779B97F4A7C15), _U(0xBF58476D1CE4E5B9), _U(0x94D049BB133111EB)  # splitmix64


def _mix(x):
    """splitmix64 finaliser on numpy uint64 (wraparound is defined for unsigned numpy ints)."""
    x = x + _GOLD
    x = (x ^ (x >> _U(30))) * _M1
    x = (x ^ (x >> _U(27))) * _M2
    return x ^ (x >> _U(31))


def hash_uniforms(seed, target, hop, node, k, device):
    """[len(node), k] uniforms in [0, 1), a pure function of (seed, target edge id, hop, node, slot).
    Computed on CPU in numpy uint64, so results are identical on CPU, CUDA and MPS."""
    t = target.cpu().numpy().astype(np.int64).view(np.uint64)
    n = node.cpu().numpy().astype(np.int64).view(np.uint64)
    with np.errstate(over="ignore"):
        x = _mix(_U(seed) ^ (t * _GOLD) ^ (_U(hop) << _U(32))) ^ n
        x = _mix(x[:, None] ^ (np.arange(k, dtype=np.uint64) * _GOLD)[None, :])
    return torch.from_numpy((x >> _U(11)).astype(np.float64) / float(1 << 53)).to(device)


class SplitGraph:
    """Edges [0, n_visible) of the time-sorted edge list, with an incident-edge CSR on the device."""

    def __init__(self, src, dst, n_nodes, visible, device):
        s, d = src[visible], dst[visible]
        eid = torch.as_tensor(visible, device=device)
        node = torch.cat([s, d])
        inc = torch.cat([eid, eid])
        self.n_edges = int(len(src))
        key = node * self.n_edges + inc  # incident edges sorted by (node, edge id = time order)
        order = torch.argsort(key)
        self.inc, self.key = inc[order], key[order]
        key_out = s * self.n_edges + eid  # out-edges only, for strictly-past out-degree
        self.key_out = torch.sort(key_out).values
        self.rowptr_out = torch.zeros(n_nodes + 1, dtype=torch.long, device=device)
        self.rowptr_out[1:] = torch.cumsum(torch.bincount(s, minlength=n_nodes), 0)
        self.rowptr = torch.zeros(n_nodes + 1, dtype=torch.long, device=device)
        self.rowptr[1:] = torch.cumsum(torch.bincount(node, minlength=n_nodes), 0)
        deg_in = torch.bincount(d, minlength=n_nodes).float()
        deg_out = torch.bincount(s, minlength=n_nodes).float()
        self.deg = torch.stack([torch.log1p(deg_in), torch.log1p(deg_out)], 1) / 5

    def deg_before(self, nodes, cap):
        """log in/out-degree from edges with id < cap only (used in temporal mode instead of split-graph degree)."""
        n = self.n_edges
        tot = torch.searchsorted(self.key, nodes * n + cap) - self.rowptr[nodes]
        out = torch.searchsorted(self.key_out, nodes * n + cap) - self.rowptr_out[nodes]
        return torch.stack([torch.log1p((tot - out).float()), torch.log1p(out.float())], 1) / 5

    def sample_rows(self, nodes, k, gen, caps, salt=None):
        # salt: None (shared generator, training) or (seed, target_eids, hop) for deterministic eval/serve draws
        """k incident edges per node (with replacement), each node with its own cap (edge id < caps[i]).

        With `salt` (one int64 per node), draws are a pure function of (salt, slot) instead of the shared
        generator, so a node's sample does not depend on what else is in the batch.
        Returns (edge ids, row index into `nodes` that each edge was sampled for)."""
        start = self.rowptr[nodes]
        deg = torch.searchsorted(self.key, nodes * self.n_edges + caps) - start
        rows = torch.nonzero(deg > 0).flatten()
        if salt is None:
            u = torch.rand(len(rows), k, device=nodes.device, generator=gen)
        else:
            seed, target, hop = salt
            u = hash_uniforms(seed, target[rows], hop, nodes[rows], k, nodes.device)
        off = (u * deg[rows, None]).long()
        return self.inc[(start[rows, None] + off).flatten()], rows.repeat_interleave(k)

    def sample(self, nodes, k, gen):
        """k incident edges per node, with replacement (non-temporal batches and the explainer)."""
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

    def forward(self, x, ei, ea, target, n_mp=None):
        """n_mp: only the first n_mp edges pass messages (temporal mode keeps target edges out of message passing)."""
        h, e = torch.relu(self.node_in(x)), torch.relu(self.edge_in(ea))
        mp = slice(None) if n_mp is None else slice(0, n_mp)
        fwd_ei = ei[:, mp]
        rev = fwd_ei.flip(0)
        for f, r, m, u, nn_, en in zip(self.fwd, self.rev, self.mix, self.edge_upd, self.n_norm, self.e_norm):
            em = e[mp]
            h = nn_(h + self.drop(torch.relu(m(torch.cat([f(h, fwd_ei, em), r(h, rev, em)], 1)))))
            e = en(e + self.drop(u(torch.cat([h[ei[0]], h[ei[1]], e], 1))))
        t = target
        return self.head(torch.cat([h[ei[0, t]], h[ei[1, t]], e[t]], 1)).squeeze(1)


class Data:
    """All edges on the device; batches = seed edges + sampled 2-hop neighbourhood in a split graph."""

    def __init__(self, src, dst, node_x, edge_x, device, fanout=(20, 8), ego=False, temporal=False, ts=None):
        self.src, self.dst = torch.as_tensor(src, device=device), torch.as_tensor(dst, device=device)
        self.node_x, self.edge_x = torch.as_tensor(node_x, device=device), torch.as_tensor(edge_x, device=device)
        self.n_nodes, self.device, self.fanout, self.ego, self.temporal = len(node_x), device, fanout, ego, temporal
        self.sample_seed = None  # set by predict(): deterministic per-target sampling in eval/serve
        # per-target temporal cap = first edge id sharing that target's timestamp, so same-minute edges are excluded too
        self.first_same_ts = None if ts is None else torch.as_tensor(np.searchsorted(ts, ts, side="left"), device=device)

    def batch(self, g, seeds, gen):
        if self.temporal:
            return self._temporal_batch(g, seeds, gen)
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


    def temporal_parts(self, g, seeds, gen):
        """Per-target strict subgraphs, built as one disjoint union.

        Each target b gets its own copy of every node it touches (key = b * n_nodes + node), and every edge
        sampled for it has id < its own cap (first edge sharing its timestamp). A target's score therefore
        depends only on strictly earlier edges, never on its batch-mates. Returns
        (past_eids, past_owner, caps)."""
        caps = seeds if self.first_same_ts is None else self.first_same_ts[seeds]
        n, B = self.n_nodes, len(seeds)
        b = torch.arange(B, device=seeds.device)
        keys = torch.unique(torch.cat([b * n + self.src[seeds], b * n + self.dst[seeds]]))
        pairs = []
        for hop, k in enumerate(self.fanout):
            owner, node = keys // n, keys % n
            # eval/serve: draws depend only on (seed, target edge, hop, node, slot)
            salt = None if self.sample_seed is None else (self.sample_seed, seeds[owner], hop)
            eids, row = g.sample_rows(node, k, gen, caps[owner], salt)
            own = owner[row]
            pairs.append(own * g.n_edges + eids)
            if hop < len(self.fanout) - 1:  # the last hop's frontier is never sampled from
                keys = torch.unique(torch.cat([keys, own * n + self.src[eids], own * n + self.dst[eids]]))
        pairs = torch.unique(torch.cat(pairs)) if pairs else seeds[:0]
        return pairs % g.n_edges, pairs // g.n_edges, caps

    def _temporal_batch(self, g, seeds, gen):
        """PRD-strict: per-target caps (see temporal_parts). Targets are appended after the past edges and
        excluded from message passing via n_mp, so no target sees another target or anything at/after its time."""
        n = self.n_nodes
        past, owner, caps = self.temporal_parts(g, seeds, gen)
        b = torch.arange(len(seeds), device=seeds.device)
        e_owner = torch.cat([owner, b])
        eids = torch.cat([past, seeds])
        ends = torch.cat([e_owner * n + self.src[eids], e_owner * n + self.dst[eids]])
        nkey, local = torch.unique(ends, return_inverse=True)
        ei = local.view(2, -1)
        target = torch.arange(len(past), len(eids), device=seeds.device)
        node, node_owner = nkey % n, nkey // n
        x = torch.cat([self.node_x[node], g.deg_before(node, caps[node_owner])], 1)
        if self.ego:
            flag = torch.zeros(len(nkey), 1, device=x.device)
            flag[ei[:, target].flatten()] = 1
            x = torch.cat([x, flag], 1)
        return x, ei, self.edge_x[eids], target, len(past)


@torch.no_grad()
def predict(model, data, g, rows, bs=4096, seed=0):
    model.eval()
    gen = torch.Generator(device=data.device).manual_seed(seed)
    if data.temporal:
        bs = min(bs, 512)  # per-target subgraphs are not shared, so keep batches moderate for memory
    prev = data.sample_seed
    if data.temporal:
        data.sample_seed = seed
    out = []
    try:
        for i in range(0, len(rows), bs):
            seeds = torch.as_tensor(rows[i:i + bs], device=data.device)
            with torch.autocast(data.device.type, dtype=torch.bfloat16, enabled=AMP):
                out.append(torch.sigmoid(model(*data.batch(g, seeds, gen)).float()).cpu())
    finally:
        data.sample_seed = prev
    return torch.cat(out).numpy()


def main(data_dir, out=None, epochs=30, patience=5, bs=None, neg_frac=0.25, seed=7, hidden=64, lr=1e-3, pos_weight=5.0,
         fanout=(20, 8), ego=False, tag="gnn", temporal=False):
    torch.manual_seed(seed)
    bs = bs or (512 if temporal else 2048)  # per-target temporal subgraphs are larger: smaller default batches
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    tx, acc = load(data_dir)
    e = canonical(tx[tx["src"] != tx["dst"]])
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

    D = Data(src, dst, node_x, edge_x, dev, tuple(fanout), ego, temporal, e["ts"].to_numpy() if temporal else None)
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
        starts = np.arange(0, len(rows), bs)  # per-target caps: batch composition does not matter
        tot = 0.0
        for i in starts:
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
                    "fanout": D.fanout, "ego": ego, "temporal": temporal, "entity_types": ENTITY_TYPES}, o / f"ibm_{tag}.pt")
        np.savez_compressed(o / f"ibm_{tag}_scores.npz", y_val=y[va], s_val=pv, y_test=y[te], s_test=pt)  # for conformal.py
        (o / f"ibm_{tag}_results.json").write_text(json.dumps(
            {"paper": PAPER_GNN, "split_cutoffs": PAPER_CUTOFFS, "results": res, "history": history,
             "hparams": dict(epochs=epochs, bs=bs, neg_frac=neg_frac, seed=seed, hidden=hidden, lr=lr, pos_weight=pos_weight, fanout=list(fanout), ego=ego, temporal=temporal),
             "operating_threshold": thr}, indent=2))
    return res


class IBMGNNScorer:
    """Serve the trained GNN. `edges` must hold the history the target rows are judged against.

        scorer = IBMGNNScorer(name="gnn_temporal")
        scorer.score(edges, accounts, rows=[...])  # rows = positions in `edges`, as in IBMScorer / TraceLayerScorer
    edges: ts, src, dst, amt_paid, amt_recv, cur_paid, cur_recv, fmt (any order; sorted by time internally).
    Temporal checkpoints score each row from strictly earlier edges only (per-row cap), so a row's score does
    not depend on which other rows are scored with it.
    """

    def __init__(self, artifact_dir=Path(__file__).parent.parent / "artifacts" / "ibm", device="cpu", name="gnn"):
        d = Path(artifact_dir)
        ck = torch.load(d / f"ibm_{name}.pt", map_location=device, weights_only=False)
        self.ck, self.device = ck, torch.device(device)
        self.model = EdgeGATv2(**ck["config"]).to(self.device).eval()
        self.model.load_state_dict(ck["state_dict"])
        res = json.loads((d / f"ibm_{name}_results.json").read_text())
        self.threshold = res["operating_threshold"]
        # surfaced in every score() row so the API/UI can show it
        self.calibration = res.get("calibration") or ("batch-cap threshold; per-target re-eval pending"
                                                      if ck.get("temporal") else "per-protocol threshold")

    def score(self, edges, accounts, rows=None):
        e = edges.reset_index(drop=True).assign(ts=lambda d: pd.to_datetime(d["ts"]))
        rows = np.arange(len(e)) if rows is None else np.asarray(rows, dtype=float).ravel()
        if len(rows) == 0:
            return pd.DataFrame({"gnn_score": [], "gnn_flagged": [], "calibration": []}, index=pd.Index([], name="row", dtype=np.int64))
        if not (np.all(rows == np.floor(rows)) and rows.min() >= 0 and rows.max() < len(e)):
            raise ValueError(f"rows must be integer positions in 0..{len(e) - 1}")
        rows = rows.astype(np.int64)
        # canonical order (ml/ibm/order.py), the same as training: edge ids, and so the hashed sampling,
        # must not depend on how the caller ordered same-timestamp rows
        order = canonical_order(e)
        e = e.iloc[order].reset_index(drop=True)
        sorted_pos = np.empty_like(order)
        sorted_pos[order] = np.arange(len(order))
        ids = pd.Index(pd.unique(np.r_[accounts["Account Number"].to_numpy(), e["src"].to_numpy(), e["dst"].to_numpy()]))
        ent = accounts.drop_duplicates("Account Number").set_index("Account Number")["Entity Name"].str.replace(r" #.*", "", regex=True).reindex(ids)
        node_x = np.column_stack([(ent == t).to_numpy(float) for t in self.ck["entity_types"]]).astype(np.float32)
        ef = gnn_edge_features(e)[self.ck["edge_features"]]
        edge_x = ((ef - pd.Series(self.ck["edge_mu"])) / pd.Series(self.ck["edge_sd"])).to_numpy(np.float32)
        D = Data(ids.get_indexer(e["src"]), ids.get_indexer(e["dst"]), node_x, edge_x, self.device, tuple(self.ck["fanout"]), self.ck.get("ego", False), self.ck.get("temporal", False),
                 e["ts"].to_numpy() if self.ck.get("temporal", False) else None)
        g = SplitGraph(D.src, D.dst, D.n_nodes, np.arange(len(e)), self.device)
        p = predict(self.model, D, g, sorted_pos[rows])
        return pd.DataFrame({"gnn_score": p, "gnn_flagged": p >= self.threshold, "calibration": self.calibration},
                            index=pd.Index(rows, name="row"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--pos-weight", type=float, default=5.0)
    ap.add_argument("--neg-frac", type=float, default=0.25)
    ap.add_argument("--bs", type=int, default=None, help="default 2048, or 512 with --temporal (per-target subgraphs)")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--fanout", type=int, nargs="+", default=[20, 8])
    ap.add_argument("--ego", action="store_true")
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--temporal", action="store_true", help="PRD-strict: message passing only over edges strictly before each batch")
    ap.add_argument("--tag", default="gnn", help="artifact name: ibm_<tag>.pt / _results.json / _scores.npz")
    a = ap.parse_args()
    main(a.data, a.out, epochs=a.epochs, patience=a.patience, bs=a.bs, neg_frac=a.neg_frac, hidden=a.hidden, lr=a.lr,
         pos_weight=a.pos_weight, fanout=a.fanout, ego=a.ego, tag=a.tag, seed=a.seed, temporal=a.temporal)
