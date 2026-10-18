"""Temporal GNN batching (ml/ibm/gnn.py): per-target strictness and position-based serving."""
import numpy as np
import pandas as pd
import torch

from ml.ibm.gnn import Data, EdgeGATv2, SplitGraph

rng = np.random.default_rng(0)
E, N = 4000, 200
SRC, DST = rng.integers(0, N, E), rng.integers(0, N, E)
TS = np.sort(rng.integers(0, 2000, E))  # many edges share a timestamp, like HI-Small's minute resolution


def make(ego=True):
    D = Data(SRC, DST, np.zeros((N, 6), np.float32), rng.random((E, 16)).astype(np.float32), torch.device("cpu"),
             (20, 8), ego=ego, temporal=True, ts=TS)
    return D, SplitGraph(D.src, D.dst, N, np.arange(E), torch.device("cpu"))


def test_each_target_sees_only_edges_before_its_own_timestamp():
    D, g = make()
    seeds = torch.tensor([100, 1500, 3000, 3999])
    _, past, owner, caps = D.temporal_parts(g, seeds, torch.Generator().manual_seed(1))
    assert len(past) > 0
    for b, s in enumerate(seeds.tolist()):
        mine = past[owner == b]
        assert (mine < caps[b]).all() and (TS[mine.numpy()] < TS[s]).all()  # strictly earlier time, not just id
    # the earliest target still gets context of its own: caps differ per target
    assert len(set(caps.tolist())) == len(seeds)


def test_target_context_does_not_depend_on_batch_mates():
    D, g = make()
    alone = D.temporal_parts(g, torch.tensor([3000]), torch.Generator().manual_seed(1))
    with_early = D.temporal_parts(g, torch.tensor([3000, 10]), torch.Generator().manual_seed(1))
    cap_alone, cap_batched = alone[3][0], with_early[3][0]
    assert cap_alone == cap_batched  # old behaviour capped the whole batch at edge 10's time


def test_batch_runs_through_model():
    D, g = make()
    x, ei, ea, t, n_mp = D.batch(g, torch.tensor([500, 2500]), torch.Generator().manual_seed(0))
    out = EdgeGATv2(6 + 2 + 1, 16)(x, ei, ea, t, n_mp)
    assert out.shape == (2,) and torch.isfinite(out).all()
