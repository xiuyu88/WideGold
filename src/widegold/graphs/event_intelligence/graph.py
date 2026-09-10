from __future__ import annotations

from widegold.graphs.event_intelligence.nodes import assess_event_mock, extract_event_mock, finalize_event_mock, map_factors_mock
from widegold.graphs.event_intelligence.production import (
    assess_event_llm,
    detect_conflict,
    expert_review,
    extract_event_llm,
    finalize_event_llm,
    map_factors_llm,
    validate_event,
)
from widegold.graphs.event_intelligence.state import EventGraphState
from widegold.schemas.common import AnalysisContext
from widegold.schemas.events import NewsCluster, StructuredEvent
from widegold.settings.app import get_settings


class DirectEventGraph:
    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode

    def invoke(self, state: EventGraphState) -> EventGraphState:
        current = dict(state)
        nodes = (
            (extract_event_mock, map_factors_mock, assess_event_mock, finalize_event_mock)
            if self.mode == "mock"
            else (extract_event_llm, map_factors_llm, assess_event_llm, validate_event, detect_conflict, expert_review, finalize_event_llm)
        )
        for node in nodes:
            current.update(node(current))
        return current  # type: ignore[return-value]


def build_event_graph(mode: str | None = None):
    mode = mode or get_settings().event_graph_mode
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return DirectEventGraph(mode)

    builder = StateGraph(EventGraphState)
    if mode == "mock":
        nodes = [("extract", extract_event_mock), ("map_factors", map_factors_mock), ("assess", assess_event_mock), ("finalize", finalize_event_mock)]
    else:
        nodes = [
            ("extract", extract_event_llm),
            ("map_factors", map_factors_llm),
            ("assess", assess_event_llm),
            ("validate", validate_event),
            ("conflict", detect_conflict),
            ("expert_review", expert_review),
            ("finalize", finalize_event_llm),
        ]
    for name, func in nodes:
        builder.add_node(name, func)
    builder.add_edge(START, nodes[0][0])
    for (left, _), (right, _) in zip(nodes, nodes[1:]):
        builder.add_edge(left, right)
    builder.add_edge(nodes[-1][0], END)
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
