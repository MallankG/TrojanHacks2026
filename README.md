# TraceLayer (TrojanHacks 2026)

Governed financial-crime investigation layer. Spec: `docs/TraceLayer_PRD_v3_3_Implementation_Playbook.docx`.

- `frontend/` — Next.js app
- `backend/` — FastAPI app (`main.py`) and ML (`ml/`)
- `docs/` — PRD

## Backend

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn main:app --reload          # curl http://127.0.0.1:8000/health
```

### ML layout (`backend/ml/`)

| package | data | what |
|---|---|---|
| `ml/tracelayer/` | synthetic AMLSim-style (`ml/data/`, generated) | rules, Isolation Forest, XGBoost, GNN, ensemble; `TraceLayerScorer` serves them |
| `ml/ibm/` | IBM AML HI-Small (Kaggle) | XGBoost reproducing Shahbazi et al. 2026 (arXiv:2609.37155); GATv2 challenger; `IBMScorer` serves XGBoost |

Artifacts: `ml/artifacts/tracelayer/`, `ml/artifacts/ibm/`. Run from `backend/`:

```bash
python -m ml.tracelayer.generate_data --out ml/data
python -m ml.tracelayer.train --data ml/data --out ml/artifacts/tracelayer
python -m ml.tracelayer.scorer                    # self-check: serving PR-AUC == training

python -m ml.ibm.xgb --data <HI-Small dir> --out ml/artifacts/ibm   # or ml/ibm/colab_xgb.ipynb on Colab
python -m ml.ibm.scorer --data <HI-Small dir>     # self-check: serving AUPRC == Colab
python -m ml.ibm.gnn --data <HI-Small dir> --out ml/artifacts/ibm   # GPU recommended
```

IBM HI-Small XGBoost, test (5 seeds): paper 38.46 AUPRC; ours (paper config) 38.05 ± 0.91; tuned on val 47.63 ± 0.38.
