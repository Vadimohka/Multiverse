from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from .catalog import CATALOG_BY_TYPE
from .nodes import NODE_REGISTRY
from .types import DataType, ExecutionContext, item_count, schema_preview

NodeCallback = Callable[
    [str, str, dict[str, Any], dict[str, Any], int, dict[str, Any]],
    Awaitable[None],
]


def validate_dag(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
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
        source_contract = CATALOG_BY_TYPE.get(source_type or "")
        target_contract = CATALOG_BY_TYPE.get(target_type or "")
        if source_contract and target_contract:
            produced = source_contract["output_type"]
            expected = target_contract["input_type"]
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
    def __init__(self, registry: dict[str, Any] | None = None):
        self.registry = registry or NODE_REGISTRY

    async def execute(
        self,
        graph: dict[str, Any],
        context: ExecutionContext,
        initial_inputs: dict[str, Any] | None = None,
        callback: NodeCallback | None = None,
    ) -> dict[str, Any]:
        errors = validate_dag(graph)
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
            if context.cancelled:
                raise asyncio.CancelledError()
            node_id = queue.popleft()
            queued.discard(node_id)
            definition = nodes[node_id]
            node_type = definition.get("type") or definition.get("data", {}).get("type")
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
            config.setdefault("_node_id", node_id)
            started = time.perf_counter()
            output: dict[str, Any] = {}
            error: dict[str, Any] = {}
            attempts = max(int(config.get("retries", 0)), 0) + 1
            for attempt in range(1, attempts + 1):
                try:
                    timeout = float(config.get("timeout", 120))
                    output = await asyncio.wait_for(node.execute(context, merged_inputs, config), timeout=timeout)
                    break
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
                            await callback(node_id, node_type, merged_inputs, output, duration_ms, error)
                        raise
                    await asyncio.sleep(min(2 ** (attempt - 1), 10))
            outputs[node_id] = output
            contract = CATALOG_BY_TYPE.get(node_type, {})
            value = output.get(contract.get("output_item_path", "")) if contract else None
            output.setdefault("_contract", {
                "input_type": contract.get("input_type", DataType.OBJECT.value),
                "output_type": contract.get("output_type", DataType.OBJECT.value),
                "output_item_path": contract.get("output_item_path", ""),
                "item_count": item_count(value, DataType(contract.get("output_type", DataType.OBJECT.value))),
                "schema_preview": schema_preview(value),
            })
            execution_order.append(node_id)
            duration_ms = int((time.perf_counter() - started) * 1000)
            if callback:
                await callback(node_id, node_type, merged_inputs, output, duration_ms, {})
            for edge in outgoing[node_id]:
                handle = edge.get("sourceHandle")
                active = not (handle in {"true", "false"} and output.get(handle) is None)
                resolve_edge(edge, active)

        unresolved = [node_id for node_id, count in remaining.items() if count > 0 and node_id not in skipped]
        if unresolved:
            raise RuntimeError(f"Не удалось разрешить зависимости узлов: {unresolved}")
        executed_terminals = [node_id for node_id in execution_order if not outgoing[node_id] or all(str(edge["target"]) in skipped for edge in outgoing[node_id])]
        result_node = executed_terminals[-1] if executed_terminals else execution_order[-1] if execution_order else None
        return {
            "node_outputs": outputs,
            "result": outputs[result_node] if result_node else {},
            "result_node_id": result_node,
            "skipped_nodes": sorted(skipped),
            "artifacts": context.artifacts,
            "logs": context.logs,
        }
