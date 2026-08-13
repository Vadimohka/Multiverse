from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from .catalog import CATALOG_BY_TYPE, contract_for_graph
from .contracts import (
    AdaptiveAttempt,
    adapt_v2_output,
    graph_contract_version,
    normalise_graph,
    v2_node_config,
    validate_v2_graph,
)
from .contracts import (
    node_type as normalised_node_type,
)
from .nodes import NODE_REGISTRY
from .redaction import redact_value
from .strategies import DEFAULT_STRATEGIES, StrategyError, execute_adaptive
from .types import (
    DataType,
    ExecutionContext,
    RunCancelledError,
    RunDeadlineExceededError,
    RunLeaseLostError,
    item_count,
    schema_preview,
)

NodeCallback = Callable[
    [str, str, dict[str, Any], dict[str, Any], int, dict[str, Any]],
    Awaitable[None],
]


def validate_dag(graph: dict[str, Any], *, known_strategies: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    try:
        graph = normalise_graph(graph)
        graph_contract_version(graph)
    except ValueError as exc:
        return [str(exc)]
    errors.extend(validate_v2_graph(graph, known_strategies=known_strategies or DEFAULT_STRATEGIES.known_ids()))
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    ids = [str(node.get("id")) for node in nodes]
    if any(node_id in {"", "None"} for node_id in ids):
        errors.append("Каждый узел должен иметь id")
    if len(ids) != len(set(ids)):
        errors.append("Идентификаторы узлов должны быть уникальны")
    known = set(ids)
    indegree = {node_id: 0 for node_id in known}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source not in known or target not in known:
            errors.append(f"Ребро {source}->{target} ссылается на отсутствующий узел")
            continue
        if source == target:
            errors.append(f"Узел {source} не может ссылаться на себя")
            continue
        adjacency[source].append(target)
        indegree[target] += 1
        source_type = (next((node.get("type") or node.get("data", {}).get("type") for node in nodes if str(node.get("id")) == source), None))
        target_type = (next((node.get("type") or node.get("data", {}).get("type") for node in nodes if str(node.get("id")) == target), None))
        source_catalog = CATALOG_BY_TYPE.get(source_type or "")
        target_catalog = CATALOG_BY_TYPE.get(target_type or "")
        if source_catalog and target_catalog:
            produced = contract_for_graph(str(source_type), graph).output_type.value
            expected = contract_for_graph(str(target_type), graph).input_type.value
            # Control nodes may fan out the explicit run input; all other
            # data-bearing edges must use a matching port type.
            source_is_control = source_type in {"manual_trigger", "condition"}
            if produced != expected and not source_is_control and not (produced == DataType.OBJECT.value and expected == DataType.OBJECT.value):
                errors.append(f"Несовместимые порты {source} ({produced}) -> {target} ({expected}); добавьте adapter node")
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(known):
        errors.append("Workflow содержит цикл")
    return errors


class WorkflowEngine:
    def __init__(self, registry: dict[str, Any] | None = None, *, strategies: Any | None = None):
        self.registry = registry or NODE_REGISTRY
        # Injection keeps adaptive behaviour unit-testable without adding a
        # synthetic public canvas node or mutating global registry state.
        self.strategies = strategies or DEFAULT_STRATEGIES

    @staticmethod
    async def _stop_reason(context: ExecutionContext) -> str | None:
        if context.cancelled:
            return "CANCELLED"
        if context.stop_check:
            return await context.stop_check()
        if context.deadline_at and time.time() >= WorkflowEngine._deadline_timestamp(context.deadline_at):
            return "DEADLINE_EXCEEDED"
        return None

    @staticmethod
    def _deadline_timestamp(deadline_at: Any) -> float:
        if deadline_at.tzinfo is None:
            from datetime import UTC

            deadline_at = deadline_at.replace(tzinfo=UTC)
        return deadline_at.timestamp()

    @classmethod
    async def _raise_if_stopped(cls, context: ExecutionContext) -> None:
        reason = await cls._stop_reason(context)
        if reason == "CANCELLED":
            context.cancelled = True
            raise RunCancelledError("Run cancellation was requested")
        if reason == "DEADLINE_EXCEEDED":
            raise RunDeadlineExceededError("Run deadline was exceeded")
        if reason == "LEASE_LOST":
            raise RunLeaseLostError("Run lease was lost")

    @classmethod
    async def _execute_with_lifecycle(
        cls,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Poll lifecycle state while a network/browser node is awaiting I/O.

        Cancelling the task propagates into httpx and Playwright instead of
        merely suppressing the final success status after costly work finishes.
        """

        await cls._raise_if_stopped(context)
        configured_timeout = float(config.get("timeout", 120))
        if context.deadline_at:
            configured_timeout = min(
                configured_timeout,
                max(0.0, cls._deadline_timestamp(context.deadline_at) - time.time()),
            )
        if configured_timeout <= 0:
            raise RunDeadlineExceededError("Run deadline was exceeded")
        task = asyncio.create_task(node.execute(context, inputs, config))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {task},
                    timeout=min(max(context.heartbeat_interval_seconds, 0.2), configured_timeout),
                )
                if task in done:
                    return task.result()
                configured_timeout -= min(max(context.heartbeat_interval_seconds, 0.2), configured_timeout)
                await cls._raise_if_stopped(context)
                if configured_timeout <= 0:
                    raise RunDeadlineExceededError("Node or run deadline was exceeded")
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise

    async def execute(
        self,
        graph: dict[str, Any],
        context: ExecutionContext,
        initial_inputs: dict[str, Any] | None = None,
        callback: NodeCallback | None = None,
    ) -> dict[str, Any]:
        graph = normalise_graph(graph)
        errors = validate_dag(graph, known_strategies=self.strategies.known_ids())
        if errors:
            raise ValueError("; ".join(errors))
        nodes = {str(node["id"]): node for node in graph.get("nodes", [])}
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        remaining = {node_id: 0 for node_id in nodes}
        for edge in graph.get("edges", []):
            source, target = str(edge["source"]), str(edge["target"])
            incoming[target].append(edge)
            outgoing[source].append(edge)
            remaining[target] += 1
        active_incoming = {node_id: 0 for node_id in nodes}
        queue = deque(node_id for node_id, count in remaining.items() if count == 0)
        queued = set(queue)
        skipped: set[str] = set()
        outputs: dict[str, dict[str, Any]] = {}
        execution_order: list[str] = []
        initial_inputs = initial_inputs or {}

        def resolve_edge(edge: dict[str, Any], active: bool) -> None:
            target = str(edge["target"])
            remaining[target] -= 1
            if active:
                active_incoming[target] += 1
            if remaining[target] != 0 or target in queued or target in skipped or target in outputs:
                return
            if not incoming[target] or active_incoming[target] > 0:
                queue.append(target)
                queued.add(target)
            else:
                skip_branch(target)

        def skip_branch(node_id: str) -> None:
            if node_id in skipped or node_id in outputs:
                return
            skipped.add(node_id)
            for edge in outgoing[node_id]:
                resolve_edge(edge, False)

        while queue:
            await self._raise_if_stopped(context)
            node_id = queue.popleft()
            queued.discard(node_id)
            definition = nodes[node_id]
            node_type = normalised_node_type(definition)
            node = self.registry.get(node_type)
            if not node:
                raise ValueError(f"Неизвестный тип узла: {node_type}")
            merged_inputs: dict[str, Any] = dict(initial_inputs if not incoming[node_id] else {})
            for edge in incoming[node_id]:
                source_id = str(edge["source"])
                if source_id not in outputs:
                    continue
                source_output = outputs[source_id]
                handle = edge.get("sourceHandle")
                if handle and handle in source_output:
                    branch_value = source_output[handle]
                    if isinstance(branch_value, dict):
                        merged_inputs.update(branch_value)
                    else:
                        merged_inputs[source_id] = branch_value
                else:
                    merged_inputs.update(source_output)
            config = dict(definition.get("config") or definition.get("data", {}).get("config", {}))
            if graph_contract_version(graph) == 2:
                config = v2_node_config(config)
            config.setdefault("_node_id", node_id)
            contract_version = graph_contract_version(graph)
            started = time.perf_counter()
            output: dict[str, Any] = {}
            error: dict[str, Any] = {}
            strategy_attempts: list[AdaptiveAttempt] = []
            attempts = max(int(config.get("retries", 0)), 0) + 1
            for attempt in range(1, attempts + 1):
                try:
                    if contract_version == 2:
                        output, strategy_attempts = await execute_adaptive(
                            self.strategies,
                            node_type=node_type,
                            node=node,
                            executor=self._execute_with_lifecycle,
                            context=context,
                            inputs=merged_inputs,
                            config=config,
                        )
                    else:
                        output = await self._execute_with_lifecycle(node, context, merged_inputs, config)
                    break
                except (RunCancelledError, RunDeadlineExceededError, RunLeaseLostError):
                    raise
                except StrategyError as exc:
                    strategy_attempts = exc.attempts
                    error = {
                        "code": "STRATEGIES_EXHAUSTED",
                        "message": str(exc),
                        "node_id": node_id,
                        "node_type": node_type,
                        "attempt": attempt,
                        "retryable": attempt < attempts,
                        "attempts": [item.as_dict() for item in exc.attempts],
                    }
                    if attempt >= attempts:
                        if callback:
                            secret_values = list(context.secrets.values())
                            await callback(
                                node_id,
                                node_type,
                                redact_value(merged_inputs, secret_values),
                                {},
                                int((time.perf_counter() - started) * 1000),
                                redact_value(error, secret_values),
                            )
                        raise
                except Exception as exc:
                    error = {
                        "code": "NODE_ERROR",
                        "message": str(exc),
                        "node_id": node_id,
                        "node_type": node_type,
                        "attempt": attempt,
                        "retryable": attempt < attempts,
                    }
                    if attempt >= attempts:
                        duration_ms = int((time.perf_counter() - started) * 1000)
                        if callback:
                            secret_values = list(context.secrets.values())
                            await callback(
                                node_id,
                                node_type,
                                redact_value(merged_inputs, secret_values),
                                redact_value(output, secret_values),
                                duration_ms,
                                redact_value(error, secret_values),
                            )
                        raise
                    await asyncio.sleep(min(2 ** (attempt - 1), 10))
            if contract_version == 2:
                output = adapt_v2_output(
                    node_type,
                    output,
                    run_context={
                        "run_id": context.run_id,
                        "project_id": context.project_id,
                        "workflow_version_id": context.workflow_version_id,
                        "effective_run_clock": context.effective_run_clock.isoformat()
                        if context.effective_run_clock
                        else None,
                    },
                )
            outputs[node_id] = output
            catalog_contract = CATALOG_BY_TYPE.get(node_type, {})
            runtime_contract = contract_for_graph(node_type, graph) if catalog_contract else None
            value = output.get(runtime_contract.output_item_path) if runtime_contract else None
            output.setdefault("_contract", {
                "version": graph_contract_version(graph),
                "phase": (catalog_contract or {}).get("public_phase", "Legacy"),
                "input_type": runtime_contract.input_type.value if runtime_contract else DataType.OBJECT.value,
                "output_type": runtime_contract.output_type.value if runtime_contract else DataType.OBJECT.value,
                "output_item_path": runtime_contract.output_item_path if runtime_contract else "",
                "item_count": item_count(value, runtime_contract.output_type if runtime_contract else DataType.OBJECT),
                "schema_preview": schema_preview(value),
            })
            if strategy_attempts:
                output["_adaptive_attempts"] = [item.as_dict() for item in strategy_attempts]
            execution_order.append(node_id)
            duration_ms = int((time.perf_counter() - started) * 1000)
            if callback:
                secret_values = list(context.secrets.values())
                await callback(
                    node_id,
                    node_type,
                    redact_value(merged_inputs, secret_values),
                    redact_value(output, secret_values),
                    duration_ms,
                    {},
                )
            for edge in outgoing[node_id]:
                handle = edge.get("sourceHandle")
                active = not (handle in {"true", "false"} and output.get(handle) is None)
                resolve_edge(edge, active)

        unresolved = [node_id for node_id, count in remaining.items() if count > 0 and node_id not in skipped]
        if unresolved:
            raise RuntimeError(f"Не удалось разрешить зависимости узлов: {unresolved}")
        executed_terminals = [node_id for node_id in execution_order if not outgoing[node_id] or all(str(edge["target"]) in skipped for edge in outgoing[node_id])]
        result_node = executed_terminals[-1] if executed_terminals else execution_order[-1] if execution_order else None
        return redact_value({
            "node_outputs": outputs,
            "result": outputs[result_node] if result_node else {},
            "result_node_id": result_node,
            "skipped_nodes": sorted(skipped),
            "artifacts": context.artifacts,
            "logs": context.logs,
        }, list(context.secrets.values()))
