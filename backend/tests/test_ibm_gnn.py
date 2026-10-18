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
    """Eval/serve sampling is a pure function of (seed, target, hop, node, slot): adding a batch-mate that
    has its own earlier history must not change the target's sampled edges or its score."""
    D, g = make()
    D.sample_seed = 0
    gen = torch.Generator().manual_seed(1)
    _, past1, own1, caps1 = D.temporal_parts(g, torch.tensor([3000]), gen)
    _, past2, own2, caps2 = D.temporal_parts(g, torch.tensor([2000, 3000]), gen)
    assert len(past2[own2 == 0]) > 0  # the batch-mate really does sample its own earlier edges
    assert set(past1[own1 == 0].tolist()) == set(past2[own2 == 1].tolist()) and caps1[0] == caps2[1]
    model = EdgeGATv2(6 + 2 + 1, 16).eval()
    with torch.no_grad():
        alone = model(*D.batch(g, torch.tensor([3000]), gen))
        batched = model(*D.batch(g, torch.tensor([2000, 3000]), gen))
    assert torch.allclose(alone[0], batched[1], atol=1e-5)


def test_uniform_hash_is_uniform_and_deterministic():
    from ml.ibm.gnn import uniform_hash
    x = torch.arange(100_000, dtype=torch.int64)
    u = uniform_hash(x)
    assert torch.equal(u, uniform_hash(x)) and float(u.min()) >= 0 and float(u.max()) < 1
    assert abs(float(u.mean()) - 0.5) < 0.01 and abs(float((u < 0.1).float().mean()) - 0.1) < 0.01


def test_batch_runs_through_model():
    D, g = make()
    x, ei, ea, t, n_mp = D.batch(g, torch.tensor([500, 2500]), torch.Generator().manual_seed(0))
    out = EdgeGATv2(6 + 2 + 1, 16)(x, ei, ea, t, n_mp)
    assert out.shape == (2,) and torch.isfinite(out).all()
