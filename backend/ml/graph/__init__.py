"""GraphBase engine (PRD v3.3 §6): GraphAdapter contract, in-memory NetworkX provider, bounded cycles/motifs,
time-respecting paths and network propagation. Pure Python, no DB coupling; app/services call it."""
from .adapter import GraphAdapter, GraphError, GraphLimits, LimitExceeded, NotFound
from .motifs import find_cycles, find_motifs
from .paths import time_respecting_paths
from .propagation import propagation_leads
from .provider import NetworkXGraphAdapter

__all__ = ["GraphAdapter", "GraphError", "GraphLimits", "LimitExceeded", "NotFound", "NetworkXGraphAdapter",
           "find_cycles", "find_motifs", "time_respecting_paths", "propagation_leads"]
