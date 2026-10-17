# TrojanHacks2026

This repository contains:

- `frontend/`: Next.js app
- `backend/`: FastAPI app

## Frontend (Next.js)

```bash
cd /home/runner/work/TrojanHacks2026/TrojanHacks2026/frontend
npm install
npm run dev
```

## Backend (FastAPI)

```bash
cd /home/runner/work/TrojanHacks2026/TrojanHacks2026/backend
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Backend health check endpoint:

```bash
curl http://127.0.0.1:8000/health
```