from __future__ import annotations

from widegold.graphs.factor_research import run_factor_research_graph
from widegold.repositories.factory import repository
from widegold.schemas.research import ResearchFlowResult, ResearchRequest


def run_factor_research(request: ResearchRequest) -> ResearchFlowResult:
    repo = repository()
    repo.create_research_request(request, status="RUNNING")
    try:
        state = run_factor_research_graph(request)
        evidence = state.get("evidence", [])
        candidate = state.get("candidate")
        llm_runs = state.get("llm_executions", [])
        repo.save_research_evidence(evidence)
        if candidate is not None:
            repo.save_factor_candidate(candidate)
        if llm_runs:
            # Research currently reuses the generic LLM run table; analysis_run_id is nullable.
            repo.save_llm_runs(None, llm_runs)
        repo.update_research_request(request.research_request_id, status="REVIEW_REQUIRED")
        return ResearchFlowResult(
            research_request_id=request.research_request_id,
            candidate_ids=[candidate.candidate_id] if candidate else [],
            evidence_count=len(evidence),
            status="REVIEW_REQUIRED",
            human_review_required=True,
        )
    except Exception:
        repo.update_research_request(request.research_request_id, status="FAILED")
        raise
