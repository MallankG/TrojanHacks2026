"""Tuned XGBoost on HI-Small: xgb_temporal + 2/3-cycle counts on each split's visible graph (Bonato & Szava 2025 cycle context).

Needs the `cycles` cache group: python -m ml.ibm.common --data <dir> --cache <cache> --groups cycles

    python -m ml.ibm.xgb_cycles --cache ml/artifacts/ibm/cache --out ml/artifacts/ibm --device cuda
"""
from .common import cli, fit_xgb

if __name__ == "__main__":
    fit_xgb("xgb_cycles", ["safe39", "temporal", "cycles"], cli(__doc__))
