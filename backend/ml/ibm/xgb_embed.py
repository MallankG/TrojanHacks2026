"""Tuned XGBoost on HI-Small: xgb_temporal + train-graph SVD embedding and 2/3-cycle counts (Bonato & Szava 2025).

    python -m ml.ibm.xgb_embed --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm --device cuda
"""
from .common import cli, fit_xgb

if __name__ == "__main__":
    fit_xgb("xgb_embed", ["safe39", "temporal", "graph"], cli(__doc__))
