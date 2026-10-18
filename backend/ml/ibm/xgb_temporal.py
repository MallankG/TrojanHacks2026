"""Tuned XGBoost on HI-Small: xgb_safe + time-respecting rolling account features.

    python -m ml.ibm.xgb_temporal --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm --device cuda
"""
from .common import cli, fit_xgb

if __name__ == "__main__":
    fit_xgb("xgb_temporal", ["safe39", "temporal"], cli(__doc__))
