"""GNNExplainer for the HI-Small GATv2 model, scored against the dataset's ground-truth laundering patterns.

Following Shahbazi et al. (2026): for illicit test edges that belong to a documented pattern, take the
top-k explanation edges (k = pattern size) and compute Jaccard overlap with the pattern's edges.
Baselines: last-layer attention weights and random edges from the same sampled subgraph.
Paper: GNNExplainer 21.4% mean Jaccard vs 1.0% for attention and random.

    python -m ml.ibm.explain --data <HI-Small dir incl. HI-Small_Patterns.txt> --out ml/artifacts/ibm --device cuda
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch_geometric.explain.algorithm.utils import clear_masks, set_masks

from .gnn import Data, IBMGNNScorer, SplitGraph, gnn_edge_features
from .order import canonical
from .xgb import load


def parse_patterns(path):
    rows, pid, typ = [], -1, None
    for line in Path(path).read_text().splitlines():
        if line.startswith("BEGIN"):
            pid, typ = pid + 1, line.split("-", 1)[1].strip().split(":")[0].strip()
        elif line.startswith("END") or not line.strip():
            continue
        else:
            c = line.split(",")
            rows.append((pd.Timestamp(c[0].replace("/", "-")), c[2], c[4], round(float(c[7]), 2), pid, typ))
    return pd.DataFrame(rows, columns=["ts", "src", "dst", "amt_paid", "pattern_id", "typology"])


def batch_with_eids(D, g, seeds, gen):
    """Data.batch, also returning the global edge ids of the subgraph edges."""
    nodes = torch.unique(torch.cat([D.src[seeds], D.dst[seeds]]))
    edges = [seeds]
    for k in D.fanout:
        new = g.sample(nodes, k, gen)
        edges.append(new)
        nodes = torch.unique(torch.cat([nodes, D.src[new], D.dst[new]]))
    eids, inv = torch.unique(torch.cat(edges), return_inverse=True)
    nid, local = torch.unique(torch.cat([D.src[eids], D.dst[eids]]), return_inverse=True)
    ei, target = local.view(2, -1), inv[: len(seeds)]
    x = torch.cat([D.node_x[nid], g.deg[nid]], 1)
    if D.ego:
        flag = torch.zeros(len(nid), 1, device=x.device)
        flag[ei[:, target].flatten()] = 1
        x = torch.cat([x, flag], 1)
    return x, ei, D.edge_x[eids], target, eids


def gnn_explainer(model, x, ei, ea, t, epochs=100, lr=0.01, size=0.005, ent=1.0):
    m = torch.nn.Parameter(torch.randn(ei.size(1), device=x.device) * 0.1)
    opt = torch.optim.Adam([m], lr=lr)
    for _ in range(epochs):
        set_masks(model, m, ei, apply_sigmoid=True)
        logit = model(x, ei, ea, t)
        p = m.sigmoid()
        loss = torch.nn.functional.softplus(-logit).sum() + size * p.sum() + ent * (-(p * (p + 1e-12).log() + (1 - p) * (1 - p + 1e-12).log())).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    clear_masks(model)
    return m.detach().sigmoid()


@torch.no_grad()
def last_layer_attention(model, x, ei, ea):
    h, e = torch.relu(model.node_in(x)), torch.relu(model.edge_in(ea))
    rev = ei.flip(0)
    for i, (f, r, mx, u, nn_, en) in enumerate(zip(model.fwd, model.rev, model.mix, model.edge_upd, model.n_norm, model.e_norm)):
        hf, (_, af) = f(h, ei, e, return_attention_weights=True)
        hr, (_, ar) = r(h, rev, e, return_attention_weights=True)
        h = nn_(h + torch.relu(mx(torch.cat([hf, hr], 1))))
        e = en(e + u(torch.cat([h[ei[0]], h[ei[1]], e], 1)))
    return (af.mean(1) + ar.mean(1)) / 2


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / max(len(a | b), 1)


def main(data, out, device="cpu", n_targets=200, seed=7, name="gnn"):
    dev = torch.device(device)
    sc = IBMGNNScorer(out, device=device, name=name)
    model, ck = sc.model, sc.ck
    tx, acc = load(data)
    e = canonical(tx[tx["src"] != tx["dst"]])
    ids = pd.Index(pd.unique(np.r_[acc["Account Number"].to_numpy(), e["src"].to_numpy(), e["dst"].to_numpy()]))
    ent = acc.drop_duplicates("Account Number").set_index("Account Number")["Entity Name"].str.replace(r" #.*", "", regex=True).reindex(ids)
    node_x = np.column_stack([(ent == t).to_numpy(float) for t in ck["entity_types"]]).astype(np.float32)
    ef = gnn_edge_features(e)[ck["edge_features"]]
    edge_x = ((ef - pd.Series(ck["edge_mu"])) / pd.Series(ck["edge_sd"])).to_numpy(np.float32)
    D = Data(ids.get_indexer(e["src"]), ids.get_indexer(e["dst"]), node_x, edge_x, dev, tuple(ck["fanout"]), ck.get("ego", False))
    g = SplitGraph(D.src, D.dst, D.n_nodes, np.arange(len(e)), dev)

    pat = parse_patterns(Path(data) / "HI-Small_Patterns.txt")
    key = lambda df: df["ts"].dt.floor("min").astype(str) + "|" + df["src"] + "|" + df["dst"] + "|" + df["amt_paid"].round(2).astype(str)
    eid_of = pd.Series(np.arange(len(e)), index=key(e))
    eid_of = eid_of[~eid_of.index.duplicated()]
    pat["eid"] = eid_of.reindex(key(pat)).to_numpy()
    pat = pat.dropna(subset=["eid"]).astype({"eid": int})
    print(f"patterns: {pat.pattern_id.nunique():,} with {len(pat):,} matched edges", flush=True)

    from .xgb import split
    _, _, te, _ = split(e["ts"])
    cand = pat[pat.eid.isin(te)].drop_duplicates("eid")
    rng = np.random.default_rng(seed)
    cand = cand.iloc[rng.permutation(len(cand))[:n_targets]]
    members = pat.groupby("pattern_id")["eid"].apply(list)
    gen = torch.Generator(device=dev).manual_seed(seed)
    rows = []
    model.eval()
    for r in cand.itertuples():
        x, ei, ea, t, eids = batch_with_eids(D, g, torch.tensor([r.eid], device=dev), gen)
        truth = members[r.pattern_id]
        k = len(truth)
        eids = eids.cpu().numpy()
        mask = gnn_explainer(model, x, ei, ea, t).cpu().numpy()
        att = last_layer_attention(model, x, ei, ea).cpu().numpy()
        top = lambda s: eids[np.argsort(-s)[:k]]
        rows.append({"eid": r.eid, "typology": r.typology, "pattern_size": k, "subgraph_edges": len(eids),
                     "truth_in_subgraph": float(np.isin(truth, eids).mean()),
                     "gnnexplainer": jaccard(top(mask), truth), "attention": jaccard(top(att), truth),
                     "random": jaccard(rng.choice(eids, min(k, len(eids)), replace=False), truth)})
    df = pd.DataFrame(rows)
    summary = {m: 100 * df[m].mean() for m in ("gnnexplainer", "attention", "random")}
    summary["n_explained"] = len(df)
    summary["truth_coverage_in_subgraph"] = 100 * df["truth_in_subgraph"].mean()
    summary["by_typology"] = {t: {m: 100 * v[m].mean() for m in ("gnnexplainer", "attention", "random")} | {"n": len(v)} for t, v in df.groupby("typology")}
    summary["paper"] = {"gnnexplainer": 21.4, "attention": 1.0, "random": 1.0}
    o = Path(out)
    (o / f"ibm_{name}_explain.json").write_text(json.dumps(summary, indent=2, default=float))
    df.to_csv(o / f"ibm_{name}_explain_rows.csv", index=False)
    print(f"mean Jaccard % | GNNExplainer {summary['gnnexplainer']:.1f}  attention {summary['attention']:.1f}  random {summary['random']:.1f} "
          f"| paper 21.4 / 1.0 / 1.0 | n={len(df)}, pattern edges reachable in subgraph {summary['truth_coverage_in_subgraph']:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="ml/artifacts/ibm")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default="gnn", help="which ibm_<model>.pt to explain")
    a = ap.parse_args()
    main(a.data, a.out, a.device, a.n, name=a.model)
