# TraceLayer working-prototype checklist

Scope: everything in PRD v3.1 except graph ML models (GNN), the multi-agent architecture and RAG, which
teammates own. One frontend (the investigation UI); every screen reads the FastAPI backend when signed in with a
backend account, and keeps seeded data only where no backend exists. All features ship together in one PR and are tested against the `backend/sample_data/` demo data.

| # | Feature (page) | Branch | Status |
|---|---|---|---|
| 0 | Integration base: live sign-in, roles, uploads, quality exceptions, alerts, cases, audit | `feat/integration` | Done |
| 1 | Detection pipeline: deterministic typology detectors + statistical anomaly + documented ranking → model runs, signals, alerts; "Run detection" in the UI; richer demo dataset | `feat/detection-pipeline` | Done |
| 2 | Command center + sidebar health/badges + top-bar search and notifications from live data | `feat/live-command-center` | Done |
| 3 | Data operations: source feeds and contracts/catalog from the backend, mapping preview before upload | `feat/live-data-ops` | Done |
| 4 | Graph workbench: real accounts and transfers, bounded neighborhood, time-respecting paths, bounded cycle / fan-in / fan-out search | `feat/live-graph` | Done |
| 5 | Entity profile: real account, timeline, baseline vs observed, flow ratios, counterparties, related cases | `feat/live-entity` | Done |
| 6 | Governance: lineage explorer, model registry, pipelines, exports with SHA-256 manifest, detector comparison | `feat/live-governance` | Done |
| 7 | Cases: evidence checklist from real coverage, report builder with hashed evidence package, SLA/due date | `feat/live-cases` | Done |

Out of scope here (teammates): GraphSAGE/GAT models, LangGraph agents and copilot, policy RAG and narrative drafting.
