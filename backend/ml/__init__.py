import os
import sys

# macOS: xgboost and torch each ship their own libomp; running both multi-threaded in one
# process deadlocks torch (or segfaults xgboost if torch loads first).
# ponytail: single OpenMP thread on darwin; train heavy models on Colab/Linux where this doesn't apply.
if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
try:
    import xgboost  # noqa: E402,F401  (load xgboost's libomp before torch)
except ImportError:  # slim deploys (requirements-slim.txt) ship without the trained-model stack
    pass
