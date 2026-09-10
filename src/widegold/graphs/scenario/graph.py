from __future__ import annotations

from copy import deepcopy
from typing import TypedDict

from widegold.engine.confidence import calculate_confidence
from widegold.engine.rules import evaluate_conflicts
from widegold.engine.scoring import calculate_asset_score
from widegold.repositories.factory import repository
from widegold.schemas.factors import FactorState
from widegold.schemas.scenario import FactorShock, ScenarioAssetImpact, ScenarioRequest, ScenarioResult
from widegold.settings.config import asset_config


class ScenarioState(TypedDict, total=False):
    request: ScenarioRequest
    base_snapshot: object
    base_states: list[FactorState]
    shocks: list[FactorShock]
    shocked_states: list[FactorState]
    result: ScenarioResult


def _parse_shocks(text: str) -> tuple[list[FactorShock], list[str]]:
    shocks: list[FactorShock] = []
    branches: list[str] = []
    if "降息" in text or "偏鸽" in text:
        shocks += [
            FactorShock(factor_id="G01_US_REAL_YIELD", delta=35, rationale="假设降息降低实际利率机会成本"),
            FactorShock(factor_id="G03_FED_POLICY_SURPRISE", delta=35, rationale="假设为超预期鸽派冲击"),
            FactorShock(factor_id="EQ10_GLOBAL_FIN_CONDITIONS", delta=20, rationale="全球金融条件假设改善"),
        ]
        branches.append("若降息源于严重衰退冲击，权益的增长负面效应可能抵消流动性利好。")
    if "美元走弱" in text:
        shocks.append(FactorShock(factor_id="G02_USD", delta=30, rationale="美元走弱降低黄金美元计价压力"))
    if "美元走强" in text:
        shocks.append(FactorShock(factor_id="G02_USD", delta=-30, rationale="美元走强提高黄金压力"))
    if "人民币贬值" in text:
        shocks += [
            FactorShock(factor_id="G11_USDCNY_TRANSLATION", delta=35, rationale="人民币贬值抬升人民币计价黄金"),
            FactorShock(factor_id="EQ09_RMB_EXTERNAL_BALANCE", delta=-20, rationale="假设贬值伴随外部平衡压力"),
        ]
    if "地缘" in text or "战争" in text:
        shocks += [
            FactorShock(factor_id="G04_RISK_UNCERTAINTY", delta=45, rationale="尾部风险提升避险需求"),
            FactorShock(factor_id="EQ15_RISK_SENTIMENT_VOL", delta=-30, rationale="权益风险偏好受压"),
        ]
    if "流动性宽松" in text or "资金面宽松" in text:
        shocks += [
            FactorShock(factor_id="EQ01_CN_FUNDING_LIQUIDITY", delta=35, rationale="国内短端资金环境改善"),
            FactorShock(factor_id="EQ12_DOMESTIC_FLOW_LEVERAGE", delta=15, rationale="风险承担空间假设改善"),
        ]
    if "CPI" in text and ("超预期" in text or "上升" in text):
        shocks += [
            FactorShock(factor_id="G05_INFLATION_REGIME", delta=15, rationale="通胀状态上行"),
            FactorShock(factor_id="G01_US_REAL_YIELD", delta=-20, rationale="基准分支假设市场上调实际利率路径"),
        ]
        branches.append("若通胀上升但实际利率反而下降、美元走弱，黄金方向可能比基准分支更积极。")
    if not shocks:
        branches.append("当前 V1 规则词典未识别明确冲击；生产版可由 Scenario LLM Parser 扩展。")
    # Deduplicate by factor, summing deltas with a hard bound.
    merged: dict[str, FactorShock] = {}
    for shock in shocks:
        if shock.factor_id in merged:
            old = merged[shock.factor_id]
            merged[shock.factor_id] = FactorShock(
                factor_id=shock.factor_id,
                delta=max(-100, min(100, old.delta + shock.delta)),
                rationale=old.rationale + "；" + shock.rationale,
            )
        else:
            merged[shock.factor_id] = shock
    return list(merged.values()), branches


def run_scenario(request: ScenarioRequest) -> ScenarioResult:
    repo = repository()
    snapshot = repo.get_snapshot(request.base_analysis_run_id) if request.base_analysis_run_id else repo.latest_published()
    if snapshot is None:
        raise ValueError("Scenario requires a published or explicitly selected base analysis run")
    states = repo.get_factor_states(snapshot.analysis_run_id)
    if not states:
        raise ValueError("Base run has no factor states")

    shocks, branches = _parse_shocks(request.assumption)
    shock_map = {s.factor_id: s.delta for s in shocks}
    shocked_states = []
    for state in states:
        updated = deepcopy(state)
        if state.factor_id in shock_map:
            updated.state = max(-100.0, min(100.0, state.state + shock_map[state.factor_id]))
        shocked_states.append(updated)

    effects = evaluate_conflicts(shocked_states)
    selected = request.selected_assets or [a["id"] for a in asset_config()["assets"]]
    base_scores = {s.asset_id: s for s in snapshot.assets}
    impacts = []
    for asset_id in selected:
        if asset_id not in base_scores:
            continue
        new_score = calculate_asset_score(asset_id, shocked_states, effects, calculate_confidence, versions=base_scores[asset_id].versions)
        impacts.append(ScenarioAssetImpact(
            asset_id=asset_id,
            base_score=base_scores[asset_id].score,
            scenario_score=new_score.score,
            delta=new_score.score - base_scores[asset_id].score,
        ))
    ranked = sorted(impacts, key=lambda x: abs(x.delta), reverse=True)
    summary = "；".join(f"{x.asset_id} {x.delta:+.1f}" for x in ranked[:4]) or "暂无可量化冲击"
    return ScenarioResult(
        assumption=request.assumption,
        factor_shocks=shocks,
        asset_impacts=impacts,
        conditional_branches=branches,
        explanation=f"这是基于当前 Factor Registry 的条件模拟，不是正式评分。变化最大的资产：{summary}。",
    )
