"""Servable ensemble ranking (ml/ibm/ensemble.py)."""
import numpy as np

from ml.ibm.ensemble import blend_scores, pct

REF = np.sort(np.linspace(0.1, 0.9, 1000).astype(np.float32))


def test_rows_beyond_val_extremes_keep_their_order():
    hi = blend_scores(REF, REF, 0.8, [0.95, 0.99, 0.999], [0.95, 0.99, 0.999])
    assert (np.diff(hi) > 0).all()  # no tie at the top, where the operating threshold sits


def test_tie_break_never_overrides_a_real_blended_difference():
    # A is ahead by one val step on model b (weight 0.3) but behind on raw scores beyond model a's extremes
    a = blend_scores(REF, REF, 0.3, [0.999], [REF[500]])
    b = blend_scores(REF, REF, 0.3, [0.95], [REF[499]])
    assert a > b and (a - b) > 0.3 / len(REF) * 0.99


def test_dtype_is_cast_to_reference():
    s = np.random.default_rng(1).random(2000)
    assert np.array_equal(pct(REF, s), pct(REF, s.astype(np.float32)))
