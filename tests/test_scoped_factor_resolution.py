from datetime import datetime, timezone

from widegold.domain.enums import DataStatus
from widegold.engine.factor_resolution import resolve_factor_states
from widegold.schemas.resilience import FactorInput


def test_scoped_factor_states_prefer_asset_specific_inputs():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    inputs = [
        FactorInput(factor_id="EQ14_TREND_MOMENTUM", asset_id="CSI300", value=50, observed_at=now,
                    status=DataStatus.VALID, reliability=0.8, source_ids=["TEST"]),
        FactorInput(factor_id="EQ14_TREND_MOMENTUM", asset_id="CSI1000", value=-50, observed_at=now,
                    status=DataStatus.VALID, reliability=0.8, source_ids=["TEST"]),
    ]
    states, _ = resolve_factor_states(inputs, [], now)
    state_map = {(s.factor_id, s.asset_id): s for s in states}
    assert state_map[("EQ14_TREND_MOMENTUM", "CSI300")].state > 0
    assert state_map[("EQ14_TREND_MOMENTUM", "CSI1000")].state < 0


def test_quality_gate_coverage_uses_matching_asset_scoped_state():
    from widegold.engine.quality import calculate_asset_coverage
    from widegold.schemas.factors import FactorState, FactorStateComponents

    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    states = [
        FactorState(
            factor_id="EQ14_TREND_MOMENTUM",
            asset_id="CSI300",
            as_of_ts=now,
            state=0,
            components=FactorStateComponents(),
            reliability=0.0,
            coverage=0.0,
            status=DataStatus.UNAVAILABLE,
        ),
        FactorState(
            factor_id="EQ14_TREND_MOMENTUM",
            asset_id="CSI1000",
            as_of_ts=now,
            state=25,
            components=FactorStateComponents(level=25),
            reliability=0.8,
            coverage=1.0,
            status=DataStatus.VALID,
        ),
    ]

    csi300 = calculate_asset_coverage("CSI300", states)
    csi1000 = calculate_asset_coverage("CSI1000", states)
    assert csi300.weighted_coverage == 0.0
    assert csi1000.weighted_coverage > 0.0

    # Physical-row order must not leak CSI1000 coverage into CSI300.
    csi300_reversed = calculate_asset_coverage("CSI300", list(reversed(states)))
    assert csi300_reversed.weighted_coverage == 0.0
