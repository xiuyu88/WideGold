from __future__ import annotations

from widegold.engine.quality import calculate_asset_coverage
from widegold.engine.rules import RuleEffects
from widegold.schemas.factors import FactorState
from widegold.schemas.scores import ConfidenceResult, FactorContribution
from widegold.settings.config import resilience_config


def calculate_confidence(
    asset_id: str,
    contributions: list[FactorContribution],
    states: list[FactorState],
    effects: RuleEffects,
) -> ConfidenceResult:
    coverage_info = calculate_asset_coverage(asset_id, states)
    coverage = 100.0 * coverage_info.weighted_coverage

    state_map = {}
    for s in states:
        if s.asset_id not in {None, asset_id}:
            continue
        current = state_map.get(s.factor_id)
        if current is None or (s.asset_id == asset_id and current.asset_id is None):
            state_map[s.factor_id] = s
    relevant = [c for c in contributions if c.sensitivity > 0]
    denom = sum(abs(c.sensitivity) for c in relevant) or 1.0
    source_quality = 100.0 * sum(
        abs(c.sensitivity) * state_map[c.factor_id].reliability * state_map[c.factor_id].coverage
        for c in relevant
    ) / denom

    pos = sum(max(c.after_group_cap, 0) for c in relevant)
    neg = sum(abs(min(c.after_group_cap, 0)) for c in relevant)
    agreement = 100.0 * (max(pos, neg) / (pos + neg)) if (pos + neg) else 50.0

    total_abs = sum(abs(c.after_group_cap) for c in relevant) or 1.0
    max_share = max((abs(c.after_group_cap) / total_abs for c in relevant), default=1.0)
    stability = max(45.0, 100.0 - 55.0 * max_share)
    context = 80.0

    raw = 0.30 * coverage + 0.25 * source_quality + 0.20 * agreement + 0.15 * stability + 0.10 * context
    penalties: list[str] = []
    caps: list[str] = []

    if effects.confidence_penalty and asset_id != "RMB_GOLD":
        raw -= effects.confidence_penalty
        penalties.append(f"conflict_penalty:{effects.confidence_penalty:.0f}")

    stale_count = sum(1 for c in relevant if state_map[c.factor_id].status.value == "STALE")
    if stale_count:
        penalty = min(12.0, stale_count * 1.5)
        raw -= penalty
        penalties.append(f"stale_factor_penalty:{penalty:.1f}")

    if coverage_info.missing_critical:
        cap = float(resilience_config()["quality_gate"]["confidence_cap_if_critical_missing"])
        if raw > cap:
            raw = cap
        caps.append(f"critical_missing_cap:{cap:.0f}")

    raw = max(0.0, min(100.0, raw))
    return ConfidenceResult(
        confidence=raw,
        coverage_score=coverage,
        source_quality_score=max(0.0, min(100.0, source_quality)),
        agreement_score=agreement,
        stability_score=stability,
        context_certainty_score=context,
        penalties=penalties,
        caps_applied=caps,
    )
