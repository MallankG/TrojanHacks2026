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


def test_build_cache_refuses_orphan_legacy_parquet(tmp_path):
    from ml.ibm import common
    pd.DataFrame({"a": [1.0]}).to_parquet(tmp_path / "ibm_feat_graph.parquet")  # legacy, unstamped
    with pytest.raises(ValueError, match="edge order"):  # raised before any data is loaded
        common.build_cache("unused", tmp_path, ["safe39"])


def test_stamp_groups_refuses_groups_older_than_the_split(tmp_path):
    import os
    from ml.ibm.common import stamp_groups
    _cache(tmp_path, {"safe39": None, "temporal": None})
    split = tmp_path / "ibm_split.npz"
    old = tmp_path / "ibm_feat_temporal.parquet"  # e.g. left over from an interrupted rebuild
    os.utime(old, (split.stat().st_mtime - 60,) * 2)
    with pytest.raises(ValueError, match="predate the split"):
        stamp_groups(tmp_path)
