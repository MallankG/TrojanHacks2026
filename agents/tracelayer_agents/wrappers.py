"""`@traced_node`: the one wrapper every workflow node goes through.

A node is `fn(state, ctx) -> OutputModel`, sync or async. `ctx` carries the injected services and
config, so nodes stay thin adapters. The decorator returns a `TracedNode`; workflows call
`node.bind(ctx)` to get the async LangGraph callable, which:

1. hashes the input state (minus the append-only trace/audit lists),
2. runs the node under `config.node_timeout_s`, or `config.llm_node_timeout_s` for nodes declared
   with `llm=True` (sync nodes in a worker thread),
3. validates the result against the node's output model,
4. returns the model's non-None fields as a state update plus one `TraceEvent`.

On failure it builds an `error` TraceEvent and raises `NodeError` carrying the node name and that
event (LangGraph drops a failing node's state update, so the caller has to append it).
A node raises `SkipNode` to record `status="skipped"` and leave the state unchanged. LangGraph's
own control-flow exceptions (`interrupt()`) pass through untouched.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel

from tracelayer_agents.audit import sha256_json
from tracelayer_agents.config import AgentConfig
from tracelayer_agents.interfaces import Services
from tracelayer_agents.schemas import NodeStatus, TraceEvent

M = TypeVar("M", bound=BaseModel)

# Keys excluded from the input hash: they only grow and don't change what a node computes.
_UNHASHED_KEYS = frozenset({"trace_events", "audit_events"})


@dataclass(frozen=True)
class NodeContext:
    services: Services
    config: AgentConfig = field(default_factory=AgentConfig)


class NodeError(Exception):
    """A node failed. `trace_event` is its `status="error"` event; `state` is filled in by the
    workflow runner with the last committed state plus that event, when available."""

    def __init__(self, node: str, trace_event: TraceEvent, message: str) -> None:
        super().__init__(f"node {node!r} failed: {message}")
        self.node = node
        self.trace_event = trace_event
        self.state: dict[str, Any] | None = None


class SkipNode(Exception):  # noqa: N818 (control flow, not an error)
    """Raised by a node that has nothing to do; recorded as `status="skipped"`."""


NodeFn = Callable[[Mapping[str, Any], NodeContext], M | Awaitable[M]]


class TracedNode(Generic[M]):
    def __init__(
        self, name: str, output_model: type[M], fn: NodeFn[M], *, llm: bool = False
    ) -> None:
        self.name = name
        self.output_model = output_model
        self.fn = fn
        self.llm = llm

    def timeout_s(self, config: AgentConfig) -> float:
        return config.llm_node_timeout_s if self.llm else config.node_timeout_s

    def bind(self, ctx: NodeContext) -> Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]:
        async def run(state: Mapping[str, Any]) -> dict[str, Any]:
            return await self._run(state, ctx)

        run.__name__ = self.name
        return run

    async def _run(self, state: Mapping[str, Any], ctx: NodeContext) -> dict[str, Any]:
        input_hash = sha256_json({k: v for k, v in state.items() if k not in _UNHASHED_KEYS})
        started_at = datetime.now(UTC)
        t0 = time.perf_counter()

        def event(status: NodeStatus, output_hash: str | None = None, error: str | None = None):
            return TraceEvent(
                trace_id=state.get("trace_id") or "untraced",
                node=self.name,
                status=status,
                started_at=started_at,
                ended_at=max(datetime.now(UTC), started_at),
                duration_ms=round((time.perf_counter() - t0) * 1000, 3),
                input_hash=input_hash,
                output_hash=output_hash,
                error=error,
            )

        try:
            if inspect.iscoroutinefunction(self.fn):
                call = self.fn(state, ctx)
            else:
                call = asyncio.to_thread(self.fn, state, ctx)
            result = await asyncio.wait_for(call, timeout=self.timeout_s(ctx.config))
            if not isinstance(result, self.output_model | dict):
                raise TypeError(
                    f"expected {self.output_model.__name__}, got {type(result).__name__}"
                )
            output = self.output_model.model_validate(result)
        except SkipNode as skip:
            skipped = event("skipped", error=str(skip) or None)
            sink = getattr(ctx.services.audit, "record_trace", None)
            if sink:
                await asyncio.to_thread(sink, skipped)
            return {"trace_events": [skipped]}
        except GraphBubbleUp:
            raise  # LangGraph control flow (e.g. `interrupt()` pausing for a human), not a failure
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                message = f"TimeoutError: exceeded {self.timeout_s(ctx.config)}s"
            else:
                message = f"{type(exc).__name__}: {exc}"
            failed = event("error", error=message)
            sink = getattr(ctx.services.audit, "record_trace", None)
            if sink:
                await asyncio.to_thread(sink, failed)
            raise NodeError(self.name, failed, message) from exc

        update = {
            name: getattr(output, name)
            for name in type(output).model_fields
            if getattr(output, name) is not None
        }
        completed = event("ok", output_hash=sha256_json(output))
        sink = getattr(ctx.services.audit, "record_trace", None)
        if sink:
            await asyncio.to_thread(sink, completed)
        return {**update, "trace_events": [completed]}


def traced_node(
    name: str, output_model: type[M], *, llm: bool = False
) -> Callable[[NodeFn[M]], TracedNode[M]]:
    """`llm=True` for nodes that call the LLM: they run under `config.llm_node_timeout_s`."""

    def decorate(fn: NodeFn[M]) -> TracedNode[M]:
        return TracedNode(name, output_model, fn, llm=llm)

    return decorate
