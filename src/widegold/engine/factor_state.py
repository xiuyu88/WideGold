from collections import defaultdict
from math import exp, log

from widegold.schemas.events import StructuredEvent
from widegold.schemas.factors import FactorState, FactorStateComponents
from widegold.settings.config import factor_config


def _clip(value: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def event_impact_0_100(event: StructuredEvent, age_days: float = 0.0) -> float:
    decay = exp(-log(2) * age_days / event.half_life_days) if event.half_life_days > 0 else 1.0
    return 100.0 * (event.strength / 5.0) * event.confidence * event.novelty * (1 - event.priced_in) * event.implementation * decay


def calculate_factor_states(base_states: dict[str, float], events: list[StructuredEvent], as_of) -> list[FactorState]:
    event_adjustment: dict[str, float] = defaultdict(float)
    event_ids: dict[str, list] = defaultdict(list)
    for event in events:
        impact = event_impact_0_100(event)
        for mapping in event.factors:
            # Mock V1: event component is deliberately capped before blending with observed state.
            event_adjustment[mapping.factor_id] += mapping.direction * impact * mapping.mapping_confidence
            event_ids[mapping.factor_id].append(event.event_id)

    factor_defs = {item["id"]: item for item in factor_config()["factors"]}
    states: list[FactorState] = []
    for factor_id, base in base_states.items():
        meta = factor_defs[factor_id]
        event_component = _clip(event_adjustment.get(factor_id, 0.0))
        # Base observations remain dominant in the mock. Production factors will own their own state models.
        blended = _clip(0.80 * float(base) + 0.20 * event_component)
        states.append(
            FactorState(
                factor_id=factor_id,
                as_of_ts=as_of,
                state=blended,
                components=FactorStateComponents(level=float(base), event=event_component),
                reliability=float(meta["reliability"]),
                coverage=1.0,
                event_ids=event_ids.get(factor_id, []),
            )
        )
    return states
