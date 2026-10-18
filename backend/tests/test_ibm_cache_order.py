import numpy as np
import pandas as pd
import pytest

from ml.ibm.common import load_cache
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


def test_stamp_groups_migrates_a_pre_stamp_canon_cache(tmp_path):
    from ml.ibm.common import stamp_groups
    _cache(tmp_path, {"safe39": ORDER_VERSION, "temporal": None})  # temporal written before per-group stamps
    stamp_groups(tmp_path)
    assert load_cache(tmp_path, ["safe39", "temporal"])[-1] == ORDER_VERSION


def test_build_cache_ignores_retired_groups(tmp_path, monkeypatch):
    from ml.ibm import common
    _cache(tmp_path, {"cycles": None})  # leftover unstamped retired group on the Colab cache

    class Loaded(Exception):
        pass

    def load_edges(data):
        raise Loaded  # got past the stale-group check
    monkeypatch.setattr(common, "load_edges", load_edges)
    with pytest.raises(Loaded):
        common.build_cache("unused", tmp_path)
