from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import uuid4

from widegold.data.providers.factory import analysis_provider
from widegold.domain.enums import AnalysisRunMode, AnalysisStatus, DataStatus, PublishMode
from widegold.graphs.event_intelligence.graph import run_event_graph_with_meta
from widegold.llm.service import LLMFallbackExhausted
from widegold.repositories.factory import repository
from widegold.runtime.cache import invalidate_current_snapshot
from widegold.runtime.locks import DistributedLock, analysis_lock_key
from widegold.schemas.common import AnalysisContext, AnalysisRunRequest
from widegold.schemas.events import NewsCluster
from widegold.schemas.resilience import FactorResolutionSummary
from widegold.schemas.scores import DashboardSnapshot
from widegold.services.trace import RunTracer
from widegold.services.news_clustering import cluster_news_documents
from widegold.services.event_dedup import deduplicate_structured_events
from widegold.services.analysis_stages import (
    choose_analysis_status,
    explanation_stage,
    public_event_summary,
    resolve_factor_stage,
    score_stage,
)
from widegold.settings.config import current_version_snapshot
from widegold.settings.runtime_config import activate_version_snapshot, reset_version_snapshot


def _llm_failure_summary(executions) -> dict:
    """Aggregate failed model executions so the failure cause is visible in the run trace.

    ``events=0`` alone cannot distinguish a quiet news day from a broken model contract.  An
    alias/error_code histogram plus one sample message makes that distinction immediate, without
    requiring a SQL session against intel.llm_runs.
    """
    failures = [item for item in executions if item.status != "SUCCESS"]
    if not failures:
        return {}
    histogram = Counter(
        f"{item.model_alias}/{item.resolved_model}:{item.error_code or 'UNKNOWN'}"
        for item in failures
    )
    return {
        "failed_calls": len(failures),
        "by_alias_error": dict(histogram.most_common(10)),
        "sample_error": (failures[0].error_message or "")[:500],
    }


def _cluster_one_document_each(documents):
    return [
        NewsCluster(
            canonical_title=doc.title,
            documents=[doc],
            first_seen_at=doc.published_at,
            last_seen_at=doc.published_at,
        )
        for doc in documents
    ]


def _input_summary(inputs) -> FactorResolutionSummary:
    counts = Counter(item.status for item in inputs)
    return FactorResolutionSummary(
        total_factors=len({item.factor_id for item in inputs}),
        state_rows=len(inputs),
        valid=counts[DataStatus.VALID],
        stale=counts[DataStatus.STALE],
        partial=counts[DataStatus.PARTIAL],
        unavailable=counts[DataStatus.UNAVAILABLE],
        conflicted=counts[DataStatus.CONFLICTED],
        warnings=[warning for item in inputs for warning in item.warnings],
    )


def _run_analysis_impl(
    request: AnalysisRunRequest | None = None,
    *,
    provider=None,
    precomputed_events=None,
    precomputed_llm_executions=None,
    precomputed_factor_states=None,
    precomputed_resolution=None,
    precomputed_scores=None,
    precomputed_quality=None,
    precomputed_explanations=None,
    precomputed_event_warnings=None,
    event_source_available_override: bool | None = None,
    context_override: AnalysisContext | None = None,
    frozen_versions=None,
) -> DashboardSnapshot:
    """Run the production-shaped V1 analysis pipeline.

    The pipeline is deliberately tolerant of partial factor failures. Coarse business trace
    events are persisted for admin observability, while full technical logs remain in Docker
    stdout/stderr and Prefect.
    """

    provider = provider or analysis_provider()
    repo = repository()
    request = request or AnalysisRunRequest()
    # Providers may expose a force-refresh switch without becoming coupled to FastAPI/Prefect.
    if hasattr(provider, "force_refresh"):
        provider.force_refresh = bool(request.force_refresh)

    if request.run_mode == AnalysisRunMode.REANALYZE:
        if request.base_run_id is None:
            raise ValueError("REANALYZE requires base_run_id")
        base_snapshot = repo.get_snapshot(request.base_run_id)
        if base_snapshot is None:
            raise ValueError(f"Base run not found: {request.base_run_id}")
        analysis_date = datetime.fromisoformat(base_snapshot.analysis_date).date()
        as_of = base_snapshot.as_of
        bundle = None
    else:
        bundle = provider.load()
        analysis_date = request.analysis_date or datetime.fromisoformat(bundle.analysis_date).date()
        as_of = bundle.as_of

    if context_override is not None:
        if request.analysis_run_id is not None and context_override.analysis_run_id != request.analysis_run_id:
            raise ValueError("context_override analysis_run_id does not match request")
        if context_override.analysis_date != analysis_date or context_override.as_of_ts != as_of:
            raise ValueError("context_override date/as_of does not match prepared provider")
        context = context_override
    else:
        context = AnalysisContext(
            analysis_run_id=request.analysis_run_id or uuid4(),
            analysis_date=analysis_date,
            as_of_ts=as_of,
            data_cutoff_ts=as_of,
            trigger_type=request.trigger_type,
            run_mode=request.run_mode,
            publish_mode=request.publish_mode,
            requested_by=request.requested_by,
            versions=frozen_versions or current_version_snapshot(),
        )
    repo.create_run(context)
    trace = RunTracer(context.analysis_run_id)
    lock_mode = request.run_mode.value if request.run_mode != AnalysisRunMode.REANALYZE else str(context.analysis_run_id)
    run_lock = DistributedLock(analysis_lock_key(str(context.analysis_date), lock_mode))
    if not run_lock.acquire():
        repo.update_run(
            context.analysis_run_id,
            AnalysisStatus.FAILED,
            error_code="ANALYSIS_ALREADY_RUNNING",
            error_summary="Another analysis run currently owns the execution lock.",
        )
        trace.emit(
            "RUN",
            "FAILED",
            "同一分析日期和模式已有任务运行中",
            progress=1.0,
            level="WARNING",
            details={"error_code": "ANALYSIS_ALREADY_RUNNING"},
        )
        raise RuntimeError("ANALYSIS_ALREADY_RUNNING")

    trace.emit(
        "RUN",
        "STARTED",
        "分析任务已开始",
        progress=0.02,
        details={
            "run_mode": request.run_mode.value,
            "publish_mode": request.publish_mode.value,
            "analysis_date": str(context.analysis_date),
        },
    )

    try:
        event_processing_warnings: list[str] = list(precomputed_event_warnings or [])
        event_processing_complete = True
        trace.emit("DATA", "STARTED", "正在准备因子输入和新闻数据", progress=0.06)
        if request.run_mode == AnalysisRunMode.REANALYZE:
            factor_inputs = repo.get_factor_inputs(request.base_run_id)
            events = repo.get_events(request.base_run_id)
            trace.emit(
                "DATA",
                "COMPLETED",
                "已复用历史 Run 的 point-in-time 输入",
                progress=0.16,
                details={"factor_inputs": len(factor_inputs), "events": len(events)},
            )
        else:
            factor_inputs = provider.factor_inputs()
            events = list(precomputed_events or [])
            repo.save_documents(bundle.news)
            trace.emit(
                "DATA",
                "COMPLETED",
                "因子输入准备完成",
                progress=0.16,
                details={
                    "factor_inputs": len(factor_inputs),
                    "documents": len(bundle.news),
                    "provider": provider.__class__.__name__,
                },
            )

            if request.run_mode != AnalysisRunMode.DATA_ONLY:
                if precomputed_events is None:
                    clusters = cluster_news_documents(bundle.news)
                    trace.emit(
                        "EVENT_INTELLIGENCE",
                        "STARTED",
                        "正在进行新闻聚类、事件抽取与因子映射",
                        progress=0.20,
                        details={"documents": len(bundle.news), "clusters": len(clusters)},
                    )
                    llm_executions = []
                    event_processing_warnings: list[str] = []
                    failed_event_clusters = 0
                    for cluster in clusters:
                        try:
                            cluster_events, cluster_llm = run_event_graph_with_meta(cluster, context=context)
                            events.extend(cluster_events)
                            llm_executions.extend(cluster_llm)
                        except LLMFallbackExhausted as exc:
                            failed_event_clusters += 1
                            llm_executions.extend(exc.executions)
                            warning = (
                                f"event_cluster_failed:{cluster.cluster_id}:llm_fallback_exhausted:"
                                f"{exc.task_type}"
                            )
                            event_processing_warnings.append(warning)
                            trace.emit(
                                "EVENT_INTELLIGENCE", "DEGRADED",
                                "单个新闻聚类的模型链失败，已隔离并继续分析",
                                progress=0.28, level="WARNING",
                                details={"cluster_id": str(cluster.cluster_id), "error": str(exc)},
                            )
                        except Exception as exc:
                            failed_event_clusters += 1
                            warning = f"event_cluster_failed:{cluster.cluster_id}:{exc.__class__.__name__}"
                            event_processing_warnings.append(warning)
                            trace.emit(
                                "EVENT_INTELLIGENCE", "DEGRADED",
                                "单个新闻聚类处理失败，已隔离并继续分析",
                                progress=0.28, level="WARNING",
                                details={"cluster_id": str(cluster.cluster_id), "error": str(exc)},
                            )
                    event_processing_complete = failed_event_clusters == 0
                    repo.save_llm_runs(context.analysis_run_id, llm_executions)
                    raw_event_count = len(events)
                    events = deduplicate_structured_events(events)
                    trace.emit(
                        "EVENT_INTELLIGENCE",
                        "COMPLETED",
                        "事件理解与重复事件合并完成",
                        progress=0.34,
                        details={
                            "events_before_dedup": raw_event_count,
                            "events": len(events),
                            "deduplicated": raw_event_count - len(events),
                            "llm_calls": len(llm_executions),
                            "failed_clusters": failed_event_clusters,
                            **(
                                {"llm_failures": _llm_failure_summary(llm_executions)}
                                if failed_event_clusters
                                else {}
                            ),
                        },
                    )
                else:
                    llm_executions = list(precomputed_llm_executions or [])
                    event_processing_warnings = list(precomputed_event_warnings or [])
                    event_processing_complete = (
                        event_source_available_override
                        if event_source_available_override is not None
                        else not event_processing_warnings
                    )
                    repo.save_llm_runs(context.analysis_run_id, llm_executions)
                    trace.emit(
                        "EVENT_INTELLIGENCE",
                        "COMPLETED" if event_processing_complete else "DEGRADED",
                        (
                            "已复用 Prefect 独立事件任务结果"
                            if event_processing_complete
                            else "事件任务部分失败，已隔离失败聚类并继续分析"
                        ),
                        progress=0.34,
                        level="INFO" if event_processing_complete else "WARNING",
                        details={
                            "documents": len(bundle.news),
                            "events": len(events),
                            "llm_calls": len(llm_executions),
                            "event_processing_complete": event_processing_complete,
                            "warnings": event_processing_warnings[:20],
                            **(
                                {"llm_failures": _llm_failure_summary(llm_executions)}
                                if not event_processing_complete
                                else {}
                            ),
                        },
                    )

        repo.save_factor_inputs(context.analysis_run_id, factor_inputs)

        if request.run_mode == AnalysisRunMode.DATA_ONLY:
            summary = _input_summary(factor_inputs)
            snapshot = DashboardSnapshot(
                analysis_run_id=context.analysis_run_id,
                analysis_date=str(context.analysis_date),
                as_of=context.as_of_ts,
                status=AnalysisStatus.DATA_READY.value,
                published=False,
                assets=[],
                explanations={},
                top_events=[],
                warnings=summary.warnings,
                factor_resolution=summary.model_dump(mode="json"),
                quality_gate={},
            )
            repo.save_snapshot(snapshot, None)
            repo.update_run(context.analysis_run_id, AnalysisStatus.DATA_READY)
            trace.emit(
                "RUN",
                "COMPLETED",
                "仅数据更新任务完成",
                progress=1.0,
                details=summary.model_dump(mode="json"),
            )
            return snapshot

        repo.save_events(context.analysis_run_id, events)

        trace.emit("FACTOR_RESOLUTION", "STARTED", "正在解析 27 个因子状态", progress=0.40)
        if precomputed_factor_states is None or precomputed_resolution is None:
            previous = repo.latest_factor_states(before=context.as_of_ts)
            resolution_result = resolve_factor_stage(
                factor_inputs=factor_inputs,
                events=events,
                as_of=context.as_of_ts,
                previous_states=previous,
                event_source_available=(
                    True
                    if request.run_mode == AnalysisRunMode.REANALYZE
                    else (
                        event_source_available_override
                        if event_source_available_override is not None
                        else (
                            bundle.news_status in {DataStatus.VALID, DataStatus.PARTIAL}
                            and event_processing_complete
                        )
                    )
                ),
            )
            factor_states = resolution_result.states
            resolution = resolution_result.summary
            resolution_message = "因子状态解析完成"
        else:
            factor_states = list(precomputed_factor_states)
            resolution = precomputed_resolution
            resolution_message = "已复用 Prefect 独立因子解析任务结果"
        repo.save_factor_states(context.analysis_run_id, factor_states)
        trace.emit(
            "FACTOR_RESOLUTION",
            "COMPLETED",
            resolution_message,
            progress=0.55,
            details=resolution.model_dump(mode="json"),
            level="WARNING" if resolution.unavailable else "INFO",
        )

        trace.emit("SCORING", "STARTED", "正在执行冲突规则与资产评分", progress=0.60)
        if precomputed_scores is None or precomputed_quality is None:
            scoring_result = score_stage(factor_states=factor_states, versions=context.versions)
            quality = scoring_result.quality
            scores = scoring_result.scores
            scoring_details = {
                "asset_count": len(scores),
                "risk_flags": scoring_result.risk_flags_by_asset,
                "confidence_penalty": scoring_result.confidence_penalty_by_asset,
            }
            scoring_message = "7 类资产评分完成"
        else:
            scores = list(precomputed_scores)
            quality = precomputed_quality
            scoring_details = {
                "asset_count": len(scores),
                "risk_flags": {score.asset_id: score.risk_flags for score in scores if score.risk_flags},
            }
            scoring_message = "已复用 Prefect 独立评分任务结果"
        trace.emit(
            "SCORING",
            "COMPLETED",
            scoring_message,
            progress=0.73,
            details=scoring_details,
        )

        trace.emit(
            "QUALITY_GATE",
            "COMPLETED" if quality.passed_for_publish else "DEGRADED",
            "质量门检查完成",
            progress=0.79,
            level="INFO" if quality.passed_for_publish else "WARNING",
            details={
                "passed_for_preview": quality.passed_for_preview,
                "passed_for_publish": quality.passed_for_publish,
                "overall_weighted_coverage": quality.overall_weighted_coverage,
                "failures": quality.failures,
                "warnings": quality.warnings,
            },
        )

        trace.emit("EXPLANATION", "STARTED", "正在生成小白化解释", progress=0.83)
        if precomputed_explanations is None:
            explanations = explanation_stage(scores)
            explanation_message = "资产解释生成完成"
        else:
            explanations = dict(precomputed_explanations)
            explanation_message = "已复用 Prefect 独立解释任务结果"
        trace.emit(
            "EXPLANATION",
            "COMPLETED",
            explanation_message,
            progress=0.91,
            details={"asset_count": len(explanations)},
        )

        top_events = public_event_summary(events)
        status, should_publish = choose_analysis_status(
            publish_mode=request.publish_mode, quality=quality
        )

        warnings = [
            *resolution.warnings,
            *quality.warnings,
            *event_processing_warnings,
            *([] if request.run_mode == AnalysisRunMode.REANALYZE else bundle.news_warnings),
        ]
        if request.publish_mode == PublishMode.AUTO and not quality.passed_for_publish:
            warnings.append(
                "AUTO_PUBLISH_BLOCKED: quality gate did not pass; previous published snapshot remains active."
            )

        snapshot = DashboardSnapshot(
            analysis_run_id=context.analysis_run_id,
            analysis_date=str(context.analysis_date),
            as_of=context.as_of_ts,
            status=status.value,
            published=should_publish,
            assets=scores,
            explanations=explanations,
            top_events=top_events,
            warnings=warnings,
            factor_resolution=resolution.model_dump(mode="json"),
            quality_gate=quality.model_dump(mode="json"),
        )
        repo.save_snapshot(snapshot, quality)
        repo.update_run(context.analysis_run_id, status, quality=quality)
        if should_publish:
            # The dashboard resolves "current" from the newest analysis date, so a replay that
            # auto-publishes an older date must not be able to install itself as the cached answer.
            invalidate_current_snapshot()

        trace.emit(
            "RUN",
            "COMPLETED" if status != AnalysisStatus.QUALITY_FAILED else "DEGRADED",
            "分析任务完成" if status != AnalysisStatus.QUALITY_FAILED else "分析完成，但数据质量不足以发布",
            progress=1.0,
            level="INFO" if status != AnalysisStatus.QUALITY_FAILED else "WARNING",
            details={
                "status": status.value,
                "published": should_publish,
                "overall_weighted_coverage": quality.overall_weighted_coverage,
            },
        )
        return snapshot
    except Exception as exc:
        trace.emit(
            "RUN",
            "FAILED",
            "分析任务失败",
            progress=1.0,
            level="ERROR",
            details={"error": str(exc), "error_type": exc.__class__.__name__},
        )
        repo.update_run(
            context.analysis_run_id,
            AnalysisStatus.FAILED,
            error_code="ANALYSIS_FAILED",
            error_summary=str(exc),
        )
        raise
    finally:
        run_lock.release()


def run_analysis(
    request: AnalysisRunRequest | None = None,
    *,
    provider=None,
    precomputed_events=None,
    precomputed_llm_executions=None,
    precomputed_factor_states=None,
    precomputed_resolution=None,
    precomputed_scores=None,
    precomputed_quality=None,
    precomputed_explanations=None,
    precomputed_event_warnings=None,
    event_source_available_override: bool | None = None,
    context_override: AnalysisContext | None = None,
) -> DashboardSnapshot:
    """Freeze the runtime configuration before any provider/engine work starts."""
    versions = context_override.versions if context_override is not None else current_version_snapshot()
    token = activate_version_snapshot(versions)
    try:
        return _run_analysis_impl(
            request,
            provider=provider,
            precomputed_events=precomputed_events,
            precomputed_llm_executions=precomputed_llm_executions,
            precomputed_factor_states=precomputed_factor_states,
            precomputed_resolution=precomputed_resolution,
            precomputed_scores=precomputed_scores,
            precomputed_quality=precomputed_quality,
            precomputed_explanations=precomputed_explanations,
            precomputed_event_warnings=precomputed_event_warnings,
            event_source_available_override=event_source_available_override,
            context_override=context_override,
            frozen_versions=versions,
        )
    finally:
        reset_version_snapshot(token)
