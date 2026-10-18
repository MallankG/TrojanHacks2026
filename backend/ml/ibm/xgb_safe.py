"""Tuned XGBoost on HI-Small: paper's 39 features, node aggregates from train-period transactions only (leakage-safe).

    python -m ml.ibm.xgb_safe --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm --device cuda
"""
from .common import cli, fit_xgb

if __name__ == "__main__":
    fit_xgb("xgb_safe", ["safe39"], cli(__doc__))
