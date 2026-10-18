"""Servable ensemble ranking (ml/ibm/ensemble.py)."""
import numpy as np

from ml.ibm.ensemble import pct


def test_scores_beyond_val_extremes_keep_their_order():
    ref = np.sort(np.linspace(0.1, 0.9, 1000).astype(np.float32))
    p = pct(ref, np.array([0.95, 0.99, 0.999], dtype=np.float32))
    assert (np.diff(p) > 0).all()  # no tie at the top, where the operating threshold sits
    lo = pct(ref, np.array([0.01, 0.05]))
    assert lo[0] < lo[1]


def test_tie_break_never_crosses_percentile_steps_and_dtype_is_cast():
    ref = np.sort(np.random.default_rng(0).random(5000).astype(np.float32))
    s = np.random.default_rng(1).random(2000)
    p = pct(ref, s)
    base = np.searchsorted(ref, s.astype(np.float32), side="right") / len(ref)
    assert ((p - base) < 1 / len(ref)).all() and (p >= base).all()
    assert np.array_equal(pct(ref, s), pct(ref, s.astype(np.float32)))  # float64 callers rank like float32 refs
