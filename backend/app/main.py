import logging
import uuid
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.routes import admin, alerts, case_workflow, cases, copilot, dashboard, detection, data_ops, data_quality, entities, enterprise, governance, governance_superpowers, governance_contracts, graph, ingestion, lineage, passports, policy, scoring
from app.api.routes import entity_profile
from app.core.audit import request_id_var
from app.core.config import get_settings
from app.core.errors import install_error_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Governed financial-crime investigation backend (synthetic-data prototype). "
                "Risk signals are decision support for human review, not findings of wrongdoing.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    # Lets the UI show the request ID next to an error so it can be matched to the audit trail.
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = request_id_var.set(rid)
    started = perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    if settings.enterprise_enabled:
        from app.services.telemetry import observe_request
        await run_in_threadpool(observe_request, request, response.status_code, (perf_counter() - started) * 1000, rid)
    response.headers["X-Request-Id"] = rid
    return response


install_error_handlers(app)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


for module in (admin, ingestion, data_quality, entities, graph, scoring, alerts, case_workflow, cases, passports, copilot, policy, lineage, dashboard, data_ops, governance, governance_superpowers, governance_contracts, enterprise, detection):
    app.include_router(module.router)
app.include_router(entity_profile.router)
