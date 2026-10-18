import numpy as np
import pandas as pd
import pytest

from ml.ibm.common import load_cache
from ml.ibm.gnn import hash_uniforms
from ml.ibm.order import ORDER_VERSION


def _cache(tmp_path, group_orders):
    np.savez(tmp_path / "ibm_split.npz", y=np.zeros(2), tr=np.arange(1), va=np.arange(1), te=np.arange(1),
             amt=np.ones(2), order=ORDER_VERSION)
    for g, order in group_orders.items():
        f = pd.DataFrame({f"{g}_x": [1.0, 2.0]})
        if order:
            f.attrs["order"] = order
        f.to_parquet(tmp_path / f"ibm_feat_{g}.parquet")


def test_load_cache_refuses_a_group_in_another_edge_order(tmp_path):
    _cache(tmp_path, {"safe39": ORDER_VERSION, "temporal": None})  # e.g. an interrupted rebuild
    with pytest.raises(ValueError, match="edge order"):
        load_cache(tmp_path, ["safe39", "temporal"])
    assert load_cache(tmp_path, ["safe39"])[-1] == ORDER_VERSION


def test_hash_uniforms_accepts_numpy_and_negative_seeds():
    import torch
    t = torch.arange(3)
    a = hash_uniforms(np.int64(3), t, 0, t, 4)
    assert np.array_equal(a, hash_uniforms(3, t, 0, t, 4))
    assert hash_uniforms(-1, t, 0, t, 4).shape == (3, 4)
