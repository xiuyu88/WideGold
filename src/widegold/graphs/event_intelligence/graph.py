from __future__ import annotations

from widegold.graphs.event_intelligence.nodes import assess_event_mock, extract_event_mock, finalize_event_mock, map_factors_mock
from widegold.graphs.event_intelligence.production import (
    assess_event_llm,
    detect_conflict,
    expert_review,
    extract_event_llm,
    finalize_event_llm,
    map_factors_llm,
    route_after_conflict,
    route_after_extract,
    route_after_mapping,
    validate_event,
)
from widegold.graphs.event_intelligence.state import EventGraphState
from widegold.schemas.common import AnalysisContext
from widegold.schemas.events import NewsCluster, StructuredEvent
from widegold.settings.app import get_settings

# Production node table. The conditional edges below are the cost contract of the whole system:
# a headline that is not an event costs exactly one LLM call, an event without a usable factor
# mapping costs two, and only a material, contested event reaches the expensive review alias.
_LLM_NODES = {
    "extract": extract_event_llm,
    "map_factors": map_factors_llm,
    "assess": assess_event_llm,
    "validate": validate_event,
    "conflict": detect_conflict,
    "expert_review": expert_review,
    "finalize": finalize_event_llm,
}

_MOCK_NODES = {
    "extract": extract_event_mock,
    "map_factors": map_factors_mock,
    "assess": assess_event_mock,
    "finalize": finalize_event_mock,
}


class DirectEventGraph:
    """Dependency-free execution path used when LangGraph is not installed.

    It must spend exactly the same LLM calls as the compiled graph, so both share the routing
    predicates from ``production`` rather than re-implementing the branch conditions.
    """

    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode

    def _run_mock(self, current: dict) -> dict:
        for name in ("extract", "map_factors", "assess", "finalize"):
            current.update(_MOCK_NODES[name](current))
        return current

    def _run_llm(self, current: dict) -> dict:
        current.update(_LLM_NODES["extract"](current))
        if route_after_extract(current) == "map_factors":
            current.update(_LLM_NODES["map_factors"](current))
            if route_after_mapping(current) == "assess":
                current.update(_LLM_NODES["assess"](current))
                current.update(_LLM_NODES["validate"](current))
                current.update(_LLM_NODES["conflict"](current))
                if route_after_conflict(current) == "expert_review":
                    current.update(_LLM_NODES["expert_review"](current))
        current.update(_LLM_NODES["finalize"](current))
        return current

    def invoke(self, state: EventGraphState) -> EventGraphState:
        current = dict(state)
        runner = self._run_mock if self.mode == "mock" else self._run_llm
        return runner(current)  # type: ignore[return-value]


def build_event_graph(mode: str | None = None):
    mode = mode or get_settings().event_graph_mode
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return DirectEventGraph(mode)

    builder = StateGraph(EventGraphState)
    if mode == "mock":
        order = ["extract", "map_factors", "assess", "finalize"]
        for name in order:
            builder.add_node(name, _MOCK_NODES[name])
        builder.add_edge(START, order[0])
        for left, right in zip(order, order[1:]):
            builder.add_edge(left, right)
        builder.add_edge(order[-1], END)
        return builder.compile()

    for name, func in _LLM_NODES.items():
        builder.add_node(name, func)
    builder.add_edge(START, "extract")
    builder.add_conditional_edges(
        "extract", route_after_extract, {"map_factors": "map_factors", "finalize": "finalize"}
    )
    builder.add_conditional_edges(
        "map_factors", route_after_mapping, {"assess": "assess", "finalize": "finalize"}
    )
    builder.add_edge("assess", "validate")
    builder.add_edge("validate", "conflict")
    builder.add_conditional_edges(
        "conflict", route_after_conflict, {"expert_review": "expert_review", "finalize": "finalize"}
    )
    builder.add_edge("expert_review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


def run_event_graph_with_meta(cluster: NewsCluster, context: AnalysisContext | None = None, mode: str | None = None):
    result = build_event_graph(mode).invoke({
        "cluster": cluster,
        "errors": [],
        "llm_executions": [],
        **({"analysis_context": context} if context else {}),
    })
    return result.get("final_events", []), result.get("llm_executions", [])


def run_event_graph(cluster: NewsCluster, context: AnalysisContext | None = None, mode: str | None = None) -> list[StructuredEvent]:
    events, _ = run_event_graph_with_meta(cluster, context=context, mode=mode)
    return events
