from __future__ import annotations

from widegold.data.providers.mock import MockAnalysisDataProvider
from widegold.domain.enums import DataStatus, PublishMode
from widegold.repositories.factory import repository
from widegold.repositories.memory import reset_memory_repository
from widegold.schemas.common import AnalysisRunRequest
from widegold.services import analysis as analysis_service
from widegold.workflows.prefect.analysis_flow import _merge_event_results, _process_event_cluster


def test_direct_analysis_isolates_one_failed_event_cluster(monkeypatch):
    reset_memory_repository()
    original = analysis_service.run_event_graph_with_meta

    def flaky(cluster, context=None, mode=None):
        if "美联储" in cluster.canonical_title:
            raise RuntimeError("simulated event graph failure")
        return original(cluster, context=context, mode=mode)

    monkeypatch.setattr(analysis_service, "run_event_graph_with_meta", flaky)
    provider = MockAnalysisDataProvider(
        fail_factors={"G03_FED_POLICY_SURPRISE", "EQ08_POLICY_SUPPORT"}
    )
    snapshot = analysis_service.run_analysis(
        AnalysisRunRequest(publish_mode=PublishMode.PREVIEW_ONLY),
        provider=provider,
    )

    assert len(snapshot.assets) == 7
    assert any("event_cluster_failed" in warning for warning in snapshot.warnings)
    # Other clusters still produce events; the failed Fed cluster does not abort the run.
    assert len(snapshot.top_events) >= 1

    states = repository().get_factor_states(snapshot.analysis_run_id)
    g03 = next(state for state in states if state.factor_id == "G03_FED_POLICY_SURPRISE")
    assert g03.status == DataStatus.UNAVAILABLE
    assert g03.coverage == 0.0


def test_prefect_event_merge_marks_partial_cluster_failure_incomplete(monkeypatch):
    # The helper itself catches graph failures so a Prefect task can complete with a degraded payload.
    import widegold.workflows.prefect.analysis_flow as flow_module

    def broken(*args, **kwargs):
        raise RuntimeError("broken cluster")

    monkeypatch.setattr(flow_module, "run_event_graph_with_meta", broken)
    provider = MockAnalysisDataProvider()
    bundle = provider.load()
    from widegold.services.news_clustering import cluster_news_documents
    from widegold.schemas.common import AnalysisContext
    from widegold.domain.enums import AnalysisRunMode, TriggerType
    from widegold.settings.config import current_version_snapshot
    from uuid import uuid4

    cluster = cluster_news_documents(bundle.news)[0]
    context = AnalysisContext(
        analysis_run_id=uuid4(),
        analysis_date=bundle.as_of.date(),
        as_of_ts=bundle.as_of,
        data_cutoff_ts=bundle.as_of,
        trigger_type=TriggerType.ADMIN_MANUAL,
        run_mode=AnalysisRunMode.FULL_REFRESH,
        publish_mode=PublishMode.PREVIEW_ONLY,
        versions=current_version_snapshot(),
    )
    failed = _process_event_cluster(
        cluster.model_dump(mode="json"), context.model_dump(mode="json")
    )
    ok = {"events": [], "llm_executions": [], "success": True, "errors": []}
    merged = _merge_event_results([ok, failed])

    assert failed["success"] is False
    assert merged["complete"] is False
    assert any("event_cluster_failed" in warning for warning in merged["warnings"])
