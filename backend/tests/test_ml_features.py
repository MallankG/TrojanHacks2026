import numpy as np

from ml.tracelayer.features import _distinct_cp_to_date, _pair_counts


def test_counts_are_strictly_before_and_order_independent():
    rng = np.random.default_rng(0)
    n = 400
    o, d = rng.integers(0, 8, n), rng.integers(0, 8, n)
    t = rng.integers(0, 50, n)  # many same-second ties

    prior, recip = _pair_counts(o, d, t)
    cp = _distinct_cp_to_date(d, o, t, d, t)  # distinct senders into d before t
    for i in range(n):
        before = t < t[i]
        assert prior[i] == (before & (o == o[i]) & (d == d[i])).sum()
        assert recip[i] == (before & (o == d[i]) & (d == o[i])).sum()
        assert cp[i] == len(set(o[before & (d == d[i])]))

    p = rng.permutation(n)
    prior2, recip2 = _pair_counts(o[p], d[p], t[p])
    assert (prior2 == prior[p]).all() and (recip2 == recip[p]).all()
