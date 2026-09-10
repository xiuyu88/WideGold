from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TypedDict

from widegold.llm.service import LLMService
from widegold.research.providers import search_provider
from widegold.research.search import SearchRequest, SearchResult
from widegold.schemas.research import (
    FactorResearchCandidate,
    ResearchEvidence,
    ResearchRequest,
    ResearchSynthesis,
)
from widegold.settings.app import get_settings


class ResearchGraphState(TypedDict, total=False):
    request: ResearchRequest
    search_queries: list[str]
    search_results: list[SearchResult]
    evidence: list[ResearchEvidence]
    synthesis: ResearchSynthesis
    candidate: FactorResearchCandidate
    llm_executions: list
    errors: list[str]


def _plan(state: ResearchGraphState) -> dict:
    req = state["request"]
    prefix = f"{req.target_factor_id} " if req.target_factor_id else ""
    return {"search_queries": [
        f"{prefix}{req.question}",
        f"{prefix}{req.question} empirical evidence",
        f"{prefix}{req.question} limitations contradictory evidence",
    ]}


async def _search_all(queries: list[str]) -> list[SearchResult]:
    provider = search_provider()
    batches = await asyncio.gather(*[
        provider.search(SearchRequest(query=q, max_results=6)) for q in queries
    ])
    dedup: dict[str, SearchResult] = {}
    for batch in batches:
        for result in batch.results:
            dedup.setdefault(result.url, result)
    return list(dedup.values())[:15]


def _search(state: ResearchGraphState) -> dict:
    try:
        results = asyncio.run(_search_all(state["search_queries"]))
        return {"search_results": results}
    except RuntimeError as exc:
        return {"search_results": [], "errors": [*state.get("errors", []), str(exc)]}


def _build_evidence(state: ResearchGraphState) -> dict:
    req = state["request"]
    now = datetime.now(timezone.utc)
    evidence = [
        ResearchEvidence(
            research_request_id=req.research_request_id,
            source_type="web",
            source_tier="C",
            title=item.title,
            url=item.url,
            published_at=item.published_at,
            retrieved_at=now,
            supports=None,
            claim_summary=item.snippet or item.title,
            evidence_summary=item.snippet or "No snippet returned; requires manual/full-text verification.",
            quality_score=max(0.35, 0.8 - (item.provider_rank - 1) * 0.04),
            metadata={"source_domain": item.source_domain, **item.metadata},
        )
        for item in state.get("search_results", [])
    ]
    return {"evidence": evidence}


def _mock_synthesis(req: ResearchRequest, evidence: list[ResearchEvidence]) -> ResearchSynthesis:
    return ResearchSynthesis(
        hypothesis=f"{req.question}：形成待验证的 V1 研究假设。",
        mechanism="Mock research mode only validates the evidence → candidate → approval engineering path.",
        supportive_evidence=[e.title for e in evidence[:1]],
        contradictory_evidence=[e.title for e in evidence[1:2]],
        limitations=["Mock evidence is not production research evidence."],
        evidence_grade="D",
        proposed_config_patch={},
        validation_required=["real_evidence_review", "walk_forward", "manual_review"],
    )


def _synthesize(state: ResearchGraphState) -> dict:
    req = state["request"]
    evidence = state.get("evidence", [])
    if get_settings().research_mode == "mock":
        return {"synthesis": _mock_synthesis(req, evidence)}
    if not evidence:
        raise RuntimeError("No research evidence available for synthesis")
    payload = {
        "question": req.question,
        "target_factor_id": req.target_factor_id,
        "evidence": [
            {"title": e.title, "url": e.url, "summary": e.evidence_summary, "quality": e.quality_score}
            for e in evidence
        ],
        "requirements": [
            "separate supportive and contradictory evidence",
            "do not claim causality beyond evidence",
            "all numerical weights are hypotheses until walk-forward validation",
        ],
    }
    result = LLMService().structured_call_sync(
        "research",
        [
            {"role": "system", "content": "你是金融因子研究审阅器。基于给定证据形成保守、可验证的结构化研究结论。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        ResearchSynthesis,
    )
    return {"synthesis": result.data, "llm_executions": [*state.get("llm_executions", []), result.execution]}


def _candidate_type(request_type: str) -> str:
    return {
        "new_factor": "NEW_FACTOR",
        "factor_review": "LOGIC_CHANGE",
        "rule_review": "CONFLICT_RULE",
        "weight_hypothesis": "WEIGHT_HYPOTHESIS",
        "source_review": "SOURCE_CHANGE",
    }[request_type]


def _build_candidate(state: ResearchGraphState) -> dict:
    req = state["request"]
    syn = state["synthesis"]
    candidate = FactorResearchCandidate(
        research_request_id=req.research_request_id,
        target_factor_id=req.target_factor_id,
        candidate_type=_candidate_type(req.request_type),
        hypothesis=syn.hypothesis,
        mechanism=syn.mechanism,
        evidence_grade=syn.evidence_grade,
        proposed_config_patch=syn.proposed_config_patch,
        validation_required=syn.validation_required,
        production_enabled=False,
    )
    return {"candidate": candidate}


class DirectResearchGraph:
    def invoke(self, state: ResearchGraphState, config=None):
        current = dict(state)
        for node in (_plan, _search, _build_evidence, _synthesize, _build_candidate):
            current.update(node(current))
        return current


def build_factor_research_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return DirectResearchGraph()
    builder = StateGraph(ResearchGraphState)
    nodes = [
        ("plan", _plan),
        ("search", _search),
        ("evidence", _build_evidence),
        ("synthesize", _synthesize),
        ("candidate", _build_candidate),
    ]
    for name, fn in nodes:
        builder.add_node(name, fn)
    builder.add_edge(START, "plan")
    for (left, _), (right, _) in zip(nodes, nodes[1:]):
        builder.add_edge(left, right)
    builder.add_edge("candidate", END)
    from widegold.runtime.langgraph_checkpoint import get_checkpointer
    checkpointer = get_checkpointer()
    return builder.compile(checkpointer=checkpointer) if checkpointer is not None else builder.compile()


def run_factor_research_graph(request: ResearchRequest) -> ResearchGraphState:
    graph = build_factor_research_graph()
    config = {"configurable": {"thread_id": f"research:{request.research_request_id}"}}
    return graph.invoke({"request": request, "errors": [], "llm_executions": []}, config=config)
