from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from widegold.data.providers.prepared import PreparedFactorInputProvider
from widegold.domain.enums import AnalysisRunMode, DataStatus, PublishMode, TriggerType
from widegold.engine.factor_calculators import calculate_all_factor_inputs, required_indicator_keys
from widegold.graphs.event_intelligence.graph import run_event_graph_with_meta
from widegold.llm.service import LLMFallbackExhausted
from widegold.repositories.factory import repository
from widegold.schemas.common import AnalysisContext, AnalysisRunRequest
from widegold.schemas.events import NewsCluster, NewsDocument, StructuredEvent
from widegold.schemas.factors import FactorState
from widegold.schemas.resilience import FactorInput, FactorResolutionSummary, QualityGateResult
from widegold.schemas.scores import AssetExplanation, AssetScore
from widegold.schemas.llm import ModelExecution
from widegold.services.analysis import run_analysis
from widegold.services.analysis_stages import explanation_stage, resolve_factor_stage, score_stage
from widegold.services.event_dedup import deduplicate_structured_events
from widegold.services.indicator_collection import IndicatorCollectionService
from widegold.services.news_clustering import cluster_news_documents
from widegold.services.news_collection import NewsCollectionService
from widegold.services.trading_calendar import CNTradingCalendar
from widegold.settings.app import get_settings
from widegold.settings.config import current_version_snapshot
from widegold.settings.runtime_config import version_snapshot_scope

try:
    from prefect import flow, task
    from prefect.futures import wait
    from prefect.task_runners import ThreadPoolTaskRunner
except ImportError:
    flow = task = None
    wait = None
    ThreadPoolTaskRunner = None


def _context_from_payload(context_payload: dict | None) -> AnalysisContext | None:
    return AnalysisContext.model_validate(context_payload) if context_payload is not None else None


def _run_in_frozen_context(context_payload: dict | None, func, *args, **kwargs):
    context = _context_from_payload(context_payload)
    if context is None:
        return func(*args, **kwargs)
    with version_snapshot_scope(context.versions):
        return func(*args, **kwargs)


def _request(request_payload: dict | None = None) -> AnalysisRunRequest:
    if request_payload:
        request = AnalysisRunRequest.model_validate(request_payload)
    else:
        request = AnalysisRunRequest(
            analysis_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
            trigger_type=TriggerType.SCHEDULED,
            run_mode=AnalysisRunMode.FULL_REFRESH,
            publish_mode=PublishMode.AUTO,
            force_refresh=True,
        )
    # Scheduled runs do not pass through the FastAPI dispatcher, so freeze the business run ID here.
    if request.analysis_run_id is None:
        request = request.model_copy(update={"analysis_run_id": uuid4()})
    return request


def _guard(request: AnalysisRunRequest) -> dict | None:
    if request.trigger_type != TriggerType.SCHEDULED:
        return None
    day = request.analysis_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if CNTradingCalendar().is_trading_day(day):
        return None
    return {
        "analysis_run_id": str(request.analysis_run_id) if request.analysis_run_id else None,
        "status": "SKIPPED",
        "published": False,
        "reason": "NOT_CN_TRADING_DAY",
    }


def _collect_one_indicator(
    indicator_id: str, asset_id: str | None, as_of_iso: str, force_refresh: bool = False,
    context_payload: dict | None = None,
) -> dict:
    def _execute():
        as_of = datetime.fromisoformat(as_of_iso)
        result = IndicatorCollectionService(max_workers=1).collect(
            {(indicator_id, asset_id)}, as_of, force_refresh=force_refresh
        )
        history = result.histories.get((indicator_id, asset_id), [])
        return {
        "indicator_id": indicator_id,
        "asset_id": asset_id,
        "history_rows": len(history),
        "fetched": result.fetched,
        "external_only": result.external_only,
        "unavailable": result.unavailable,
            "warnings": result.warnings,
        }
    return _run_in_frozen_context(context_payload, _execute)


def _collect_news(as_of_iso: str, context_payload: dict | None = None) -> dict:
    def _execute():
        as_of = datetime.fromisoformat(as_of_iso)
        result = NewsCollectionService().collect(as_of)
        return {
        "documents": [doc.model_dump(mode="json") for doc in result.documents],
        "status": result.status.value,
            "warnings": result.warnings,
        }
    return _run_in_frozen_context(context_payload, _execute)


def _calculate_inputs_from_store(as_of_iso: str, context_payload: dict | None = None) -> list[dict]:
    def _execute():
        as_of = datetime.fromisoformat(as_of_iso)
        repo = repository()
        histories = {
            key: repo.get_indicator_history(key[0], as_of=as_of, asset_id=key[1], limit=600)
            for key in required_indicator_keys()
        }
        return [row.model_dump(mode="json") for row in calculate_all_factor_inputs(histories, as_of)]
    return _run_in_frozen_context(context_payload, _execute)


def _cluster_news(news_payload: dict, context_payload: dict | None = None) -> list[dict]:
    def _execute():
        documents = [NewsDocument.model_validate(row) for row in news_payload.get("documents", [])]
        return [cluster.model_dump(mode="json") for cluster in cluster_news_documents(documents)]
    return _run_in_frozen_context(context_payload, _execute)


def _event_context(request_payload: dict, as_of_iso: str) -> dict:
    request = AnalysisRunRequest.model_validate(request_payload)
    as_of = datetime.fromisoformat(as_of_iso)
    context = AnalysisContext(
        analysis_run_id=request.analysis_run_id or uuid4(),
        analysis_date=request.analysis_date or as_of.date(),
        as_of_ts=as_of,
        data_cutoff_ts=as_of,
        trigger_type=request.trigger_type,
        run_mode=request.run_mode,
        publish_mode=request.publish_mode,
        requested_by=request.requested_by,
        versions=current_version_snapshot(),
    )
    return context.model_dump(mode="json")


def _process_event_cluster(cluster_payload: dict, context_payload: dict) -> dict:
    cluster = NewsCluster.model_validate(cluster_payload)
    context = AnalysisContext.model_validate(context_payload)
    try:
        with version_snapshot_scope(context.versions):
            events, executions = run_event_graph_with_meta(cluster, context=context)
        return {
            "events": [event.model_dump(mode="json") for event in events],
            "llm_executions": [execution.model_dump(mode="json") for execution in executions],
            "success": True,
            "errors": [],
        }
    except LLMFallbackExhausted as exc:
        return {
            "events": [],
            "llm_executions": [execution.model_dump(mode="json") for execution in exc.executions],
            "success": False,
            "errors": [f"event_cluster_failed:{cluster.cluster_id}:llm_fallback_exhausted:{exc.task_type}"],
        }
    except Exception as exc:
        return {
            "events": [],
            "llm_executions": [],
            "success": False,
            "errors": [f"event_cluster_failed:{cluster.cluster_id}:{exc.__class__.__name__}"],
        }


def _merge_event_results(results: list[dict]) -> dict:
    events: list[StructuredEvent] = []
    executions: list[ModelExecution] = []
    warnings: list[str] = []
    complete = True
    for result in results:
        events.extend(StructuredEvent.model_validate(row) for row in result.get("events", []))
        executions.extend(ModelExecution.model_validate(row) for row in result.get("llm_executions", []))
        warnings.extend(str(item) for item in result.get("errors", []))
        if not result.get("success", True):
            complete = False
    raw_count = len(events)
    events = deduplicate_structured_events(events)
    return {
        "events": [event.model_dump(mode="json") for event in events],
        "llm_executions": [execution.model_dump(mode="json") for execution in executions],
        "events_before_dedup": raw_count,
        "events_after_dedup": len(events),
        "complete": complete,
        "warnings": warnings,
    }


def _resolve_factor_payload(
    factor_input_payloads: list[dict],
    event_payload: dict,
    as_of_iso: str,
    news_payload: dict,
    context_payload: dict | None = None,
) -> dict:
    def _execute():
        as_of = datetime.fromisoformat(as_of_iso)
        repo = repository()
        factor_inputs = [FactorInput.model_validate(row) for row in factor_input_payloads]
        events = [StructuredEvent.model_validate(row) for row in event_payload.get("events", [])]
        previous = repo.latest_factor_states(before=as_of)
        result = resolve_factor_stage(
        factor_inputs=factor_inputs,
        events=events,
        as_of=as_of,
        previous_states=previous,
        event_source_available=(
            news_payload.get("status") in {DataStatus.VALID.value, DataStatus.PARTIAL.value}
            and event_payload.get("complete", True)
            ),
        )
        return {
            "states": [state.model_dump(mode="json") for state in result.states],
            "summary": result.summary.model_dump(mode="json"),
        }
    return _run_in_frozen_context(context_payload, _execute)


def _score_payload(factor_payload: dict, context_payload: dict) -> dict:
    context = AnalysisContext.model_validate(context_payload)
    with version_snapshot_scope(context.versions):
        states = [FactorState.model_validate(row) for row in factor_payload.get("states", [])]
        result = score_stage(factor_states=states, versions=context.versions)
    return {
        "scores": [score.model_dump(mode="json") for score in result.scores],
        "quality": result.quality.model_dump(mode="json"),
    }


def _explain_payload(scoring_payload: dict, context_payload: dict | None = None) -> dict:
    def _execute():
        scores = [AssetScore.model_validate(row) for row in scoring_payload.get("scores", [])]
        result = explanation_stage(scores)
        return {asset_id: explanation.model_dump(mode="json") for asset_id, explanation in result.items()}
    return _run_in_frozen_context(context_payload, _execute)


def _run_prepared(
    request_payload: dict,
    as_of_iso: str,
    factor_input_payloads: list[dict],
    news_payload: dict | None = None,
    event_payload: dict | None = None,
    factor_payload: dict | None = None,
    scoring_payload: dict | None = None,
    explanation_payload: dict | None = None,
    context_payload: dict | None = None,
):
    request = AnalysisRunRequest.model_validate(request_payload)
    provider = PreparedFactorInputProvider(
        as_of=datetime.fromisoformat(as_of_iso),
        factor_inputs=[FactorInput.model_validate(row) for row in factor_input_payloads],
        news=[NewsDocument.model_validate(row) for row in ((news_payload or {}).get("documents", []))],
        news_status=DataStatus((news_payload or {}).get("status", DataStatus.UNAVAILABLE.value)),
        news_warnings=list((news_payload or {}).get("warnings", [])),
    )
    events = None
    llm_executions = None
    if event_payload is not None:
        events = [StructuredEvent.model_validate(row) for row in event_payload.get("events", [])]
        llm_executions = [ModelExecution.model_validate(row) for row in event_payload.get("llm_executions", [])]
    factor_states = None
    resolution = None
    if factor_payload is not None:
        factor_states = [FactorState.model_validate(row) for row in factor_payload.get("states", [])]
        resolution = FactorResolutionSummary.model_validate(factor_payload.get("summary", {}))
    scores = None
    quality = None
    if scoring_payload is not None:
        scores = [AssetScore.model_validate(row) for row in scoring_payload.get("scores", [])]
        quality = QualityGateResult.model_validate(scoring_payload.get("quality", {}))
    explanations = None
    if explanation_payload is not None:
        explanations = {
            asset_id: AssetExplanation.model_validate(row)
            for asset_id, row in explanation_payload.items()
        }
    return run_analysis(
        request,
        provider=provider,
        precomputed_events=events,
        precomputed_llm_executions=llm_executions,
        precomputed_factor_states=factor_states,
        precomputed_resolution=resolution,
        precomputed_scores=scores,
        precomputed_quality=quality,
        precomputed_explanations=explanations,
        precomputed_event_warnings=list((event_payload or {}).get("warnings", [])),
        event_source_available_override=(
            (news_payload or {}).get("status") in {DataStatus.VALID.value, DataStatus.PARTIAL.value}
            and (event_payload or {}).get("complete", True)
        ) if event_payload is not None else None,
        context_override=AnalysisContext.model_validate(context_payload) if context_payload is not None else None,
    ).model_dump(mode="json")


def _run_direct(request_payload: dict | None = None):
    request = _request(request_payload)
    skipped = _guard(request)
    if skipped:
        return skipped
    return run_analysis(request).model_dump(mode="json")


if flow is not None and task is not None:
    @task(name="widegold-collect-indicator", retries=0)
    def collect_indicator_task(
        indicator_id: str, asset_id: str | None, as_of_iso: str, force_refresh: bool = False,
        context_payload: dict | None = None,
    ) -> dict:
        # Provider retry/fallback/circuit breaking happens inside IndicatorCollectionService.
        return _collect_one_indicator(indicator_id, asset_id, as_of_iso, force_refresh, context_payload)

    @task(name="widegold-collect-news", retries=0)
    def collect_news_task(as_of_iso: str, context_payload: dict | None = None) -> dict:
        return _collect_news(as_of_iso, context_payload)

    @task(name="widegold-calculate-factor-inputs", retries=1, retry_delay_seconds=2)
    def calculate_factor_inputs_task(as_of_iso: str, context_payload: dict | None = None) -> list[dict]:
        return _calculate_inputs_from_store(as_of_iso, context_payload)

    @task(name="widegold-cluster-news", retries=0)
    def cluster_news_task(news_payload: dict, context_payload: dict | None = None) -> list[dict]:
        return _cluster_news(news_payload, context_payload)

    @task(name="widegold-event-intelligence-cluster", retries=0)
    def event_intelligence_cluster_task(cluster_payload: dict, context_payload: dict) -> dict:
        # LLM provider retries/fallbacks live inside the model router; task retry remains zero
        # to avoid multiplying calls to paid upstreams.
        return _process_event_cluster(cluster_payload, context_payload)

    @task(name="widegold-deduplicate-events", retries=0)
    def deduplicate_events_task(results: list[dict]) -> dict:
        return _merge_event_results(results)

    @task(name="widegold-resolve-factor-states", retries=0)
    def resolve_factor_states_task(
        factor_input_payloads: list[dict], event_payload: dict, as_of_iso: str, news_payload: dict,
        context_payload: dict | None = None,
    ) -> dict:
        return _resolve_factor_payload(
            factor_input_payloads, event_payload, as_of_iso, news_payload, context_payload
        )

    @task(name="widegold-score-and-quality", retries=0)
    def score_and_quality_task(factor_payload: dict, context_payload: dict) -> dict:
        return _score_payload(factor_payload, context_payload)

    @task(name="widegold-explanation", retries=0)
    def explanation_task(scoring_payload: dict, context_payload: dict | None = None) -> dict:
        return _explain_payload(scoring_payload, context_payload)

    @task(name="widegold-analysis-from-prepared-inputs", retries=0)
    def prepared_analysis_task(
        request_payload: dict,
        as_of_iso: str,
        factor_input_payloads: list[dict],
        news_payload: dict | None = None,
        event_payload: dict | None = None,
        factor_payload: dict | None = None,
        scoring_payload: dict | None = None,
        explanation_payload: dict | None = None,
        context_payload: dict | None = None,
    ):
        return _run_prepared(
            request_payload, as_of_iso, factor_input_payloads, news_payload, event_payload,
            factor_payload, scoring_payload, explanation_payload, context_payload
        )

    @task(name="widegold-analysis-core", retries=1, retry_delay_seconds=5)
    def analysis_core_task(request_payload: dict | None = None):
        return _run_direct(request_payload)

    @flow(
        name="widegold-close-analysis",
        log_prints=True,
        retries=0,
        task_runner=ThreadPoolTaskRunner(max_workers=6),
    )
    def analysis_pipeline(request_payload: dict | None = None):
        request = _request(request_payload)
        skipped = _guard(request)
        if skipped:
            return skipped

        settings = get_settings()
        if settings.data_mode.lower() == "raw_indicators" and request.run_mode != AnalysisRunMode.REANALYZE:
            as_of = datetime.now(ZoneInfo("Asia/Shanghai"))
            if request.analysis_date is not None:
                as_of = as_of.replace(
                    year=request.analysis_date.year,
                    month=request.analysis_date.month,
                    day=request.analysis_date.day,
                )
            request_payload = request.model_dump(mode="json")
            context_payload = _event_context(request_payload, as_of.isoformat())

            context = AnalysisContext.model_validate(context_payload)
            with version_snapshot_scope(context.versions):
                indicator_keys = sorted(
                    required_indicator_keys(), key=lambda x: (x[0], x[1] or "")
                )
            indicator_futures = [
                collect_indicator_task.submit(
                    indicator_id, asset_id, as_of.isoformat(), request.force_refresh, context_payload
                )
                for indicator_id, asset_id in indicator_keys
            ]
            news_future = collect_news_task.submit(as_of.isoformat(), context_payload)
            wait(indicator_futures)

            # Factor calculation and event intelligence are independent after data/news collection
            # and are therefore allowed to overlap in the task runner.
            factor_inputs_future = calculate_factor_inputs_task.submit(as_of.isoformat(), context_payload)
            news_payload = news_future.result()
            cluster_payloads = cluster_news_task.submit(news_payload, context_payload).result()
            event_futures = [
                event_intelligence_cluster_task.submit(cluster_payload, context_payload)
                for cluster_payload in cluster_payloads
            ]
            event_results = [future.result() for future in event_futures]
            event_payload = deduplicate_events_task.submit(event_results).result()
            factor_inputs = factor_inputs_future.result()

            factor_payload = resolve_factor_states_task.submit(
                factor_inputs, event_payload, as_of.isoformat(), news_payload, context_payload
            ).result()
            scoring_payload = score_and_quality_task.submit(factor_payload, context_payload).result()
            explanation_payload = explanation_task.submit(scoring_payload, context_payload).result()

            result = prepared_analysis_task(
                request_payload,
                as_of.isoformat(),
                factor_inputs,
                news_payload,
                event_payload,
                factor_payload,
                scoring_payload,
                explanation_payload,
                context_payload,
            )
        else:
            result = analysis_core_task(request.model_dump(mode="json"))
        print(f"WideGold analysis finished: {result.get('analysis_run_id')} status={result['status']}")
        return result
else:
    def analysis_pipeline(request_payload: dict | None = None):
        return _run_direct(request_payload)


if __name__ == "__main__":
    print(analysis_pipeline())
