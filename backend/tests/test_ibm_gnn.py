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
    past, owner, caps = D.temporal_parts(g, seeds, torch.Generator().manual_seed(1))
    assert len(past) > 0
    for b, s in enumerate(seeds.tolist()):
        mine = past[owner == b]
        assert (mine < caps[b]).all() and (TS[mine.numpy()] < TS[s]).all()  # strictly earlier time, not just id
    # every target gets its own cap (no shared batch cap)
    assert len(set(caps.tolist())) == len(seeds)


def test_target_context_does_not_depend_on_batch_mates():
    """Eval/serve sampling is a pure function of (seed, target, hop, node, slot): adding a batch-mate that
    has its own earlier history must not change the target's sampled edges or its score."""
    D, g = make()
    D.sample_seed = 0
    gen = torch.Generator().manual_seed(1)
    past1, own1, caps1 = D.temporal_parts(g, torch.tensor([3000]), gen)
    past2, own2, caps2 = D.temporal_parts(g, torch.tensor([2000, 3000]), gen)
    assert len(past2[own2 == 0]) > 0  # the batch-mate really does sample its own earlier edges
    assert set(past1[own1 == 0].tolist()) == set(past2[own2 == 1].tolist()) and caps1[0] == caps2[1]
    model = EdgeGATv2(6 + 2 + 1, 16).eval()
    with torch.no_grad():
        alone = model(*D.batch(g, torch.tensor([3000]), gen))
        batched = model(*D.batch(g, torch.tensor([2000, 3000]), gen))
    assert torch.allclose(alone[0], batched[1], atol=1e-5)


def test_hash_uniforms_are_uniform_and_deterministic():
    from ml.ibm.gnn import hash_uniforms
    t, n = torch.arange(50_000, dtype=torch.int64), torch.arange(50_000, dtype=torch.int64) % 97
    u = hash_uniforms(7, t, 1, n, 2)
    assert np.array_equal(u, hash_uniforms(7, t, 1, n, 2)) and u.min() >= 0 and u.max() < 1
    assert abs(u.mean() - 0.5) < 0.01 and abs((u < 0.1).mean() - 0.1) < 0.01
    assert not np.array_equal(u, hash_uniforms(8, t, 1, n, 2))


def test_canonical_order_is_nan_safe_and_input_order_independent():
    from ml.ibm.order import canonical_order
    e = pd.DataFrame({"ts": pd.to_datetime(["2022-09-01 00:01"] * 4 + ["2022-09-01 00:00"]),
                      "src": ["b", None, "a", float("nan"), "z"], "dst": ["x", "y", 3, "x", "x"],
                      "amt_paid": [1.0, 2.0, np.nan, 4.0, 5.0], "amt_recv": [1.0] * 5, "cur_paid": ["USD"] * 5,
                      "cur_recv": ["USD", None, "EUR", "USD", "USD"], "from_bank": ["1"] * 5, "to_bank": [2, 2, 2, "2", 2],
                      "fmt": ["ACH"] * 5})
    o = canonical_order(e)
    assert o[0] == 4  # earliest timestamp first
    perm = np.array([3, 0, 4, 2, 1])
    assert (e.iloc[perm].reset_index(drop=True).iloc[canonical_order(e.iloc[perm].reset_index(drop=True))].to_numpy().astype(str)
            == e.iloc[o].to_numpy().astype(str)).all()
    import pytest
    with pytest.raises(ValueError):  # a missing ordering column must fail loudly, not be skipped
        canonical_order(e.drop(columns="to_bank"))


def test_predict_restores_previous_sample_seed():
    from ml.ibm.gnn import predict
    D, g = make()
    D.sample_seed = 123
    predict(EdgeGATv2(6 + 2 + 1, 16).eval(), D, g, np.array([500, 900]), seed=0)
    assert D.sample_seed == 123


def test_batch_runs_through_model():
    D, g = make()
    x, ei, ea, t, n_mp = D.batch(g, torch.tensor([500, 2500]), torch.Generator().manual_seed(0))
    out = EdgeGATv2(6 + 2 + 1, 16)(x, ei, ea, t, n_mp)
    assert out.shape == (2,) and torch.isfinite(out).all()
