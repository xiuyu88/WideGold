from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.schemas.research import ResearchRequest
from widegold.services.research import run_factor_research


def test_mock_factor_research_creates_non_production_candidate():
    reset_memory_repository()
    req = ResearchRequest(
        title="Review liquidity factor",
        question="Does funding liquidity affect small-cap A shares more strongly?",
        target_factor_id="EQ01_CN_FUNDING_LIQUIDITY",
    )
    result = run_factor_research(req)
    assert result.status == "REVIEW_REQUIRED"
    assert result.evidence_count >= 1
    row = snapshot_repository.get_research_request(req.research_request_id)
    assert row is not None
    assert row["candidates"][0]["production_enabled"] is False
    assert row["candidates"][0]["status"] == "REVIEW_REQUIRED"
