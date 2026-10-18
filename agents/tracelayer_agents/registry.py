"""Builds the `Services` bundle the workflows run against.

Any service the backend doesn't pass in falls back to its mock, and `TRACELAYER_USE_MOCKS=1`
forces mocks for everything. Each mocked service logs one warning so a half-wired deployment is
visible in the logs.

The data mocks (ingestion, quality, graph, scorers, flows) share one `MockDataStore`, so a mock
can only see batches and snapshots that another mock produced. Mixing a real ingestion service with
a mock quality or graph service will fail with `KeyError: unknown transaction_batch_id`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import fields

from tracelayer_agents.interfaces import Services
from tracelayer_agents.mocks import (
    InMemoryAuditRepository,
    InMemoryCaseRepository,
    MockAccessPolicy,
    MockBehaviorScorer,
    MockDataStore,
    MockFlowInvestigator,
    MockGraphRiskScorer,
    MockGraphService,
    MockIngestion,
    MockPolicyRetriever,
    MockQuality,
    MockRankFusion,
)

logger = logging.getLogger(__name__)

USE_MOCKS_ENV = "TRACELAYER_USE_MOCKS"


def mocks_forced() -> bool:
    return os.environ.get(USE_MOCKS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _mock_services() -> Services:
    store = MockDataStore()
    return Services(
        ingestion=MockIngestion(store),
        quality=MockQuality(store),
        graph=MockGraphService(store),
        graph_risk=MockGraphRiskScorer(store),
        behavior=MockBehaviorScorer(store),
        fusion=MockRankFusion(),
        flows=MockFlowInvestigator(store),
        policy=MockPolicyRetriever(),
        cases=InMemoryCaseRepository(),
        audit=InMemoryAuditRepository(),
        access=MockAccessPolicy(),
    )


def get_services(overrides: Services | None = None) -> Services:
    """Real implementations from `overrides` where given, mocks for the rest."""
    mocks = _mock_services()
    forced = mocks_forced()
    resolved = {}
    for f in fields(Services):
        real = getattr(overrides, f.name) if overrides is not None else None
        if real is not None and not forced:
            resolved[f.name] = real
            continue
        mock = getattr(mocks, f.name)
        why = f"{USE_MOCKS_ENV}=1" if forced else "no implementation passed"
        logger.warning("service %r uses mock %s (%s)", f.name, type(mock).__name__, why)
        resolved[f.name] = mock
    return Services(**resolved)
