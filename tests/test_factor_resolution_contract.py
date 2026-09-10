from datetime import datetime, timezone
import inspect

from widegold.engine.factor_resolution import resolve_factor_states


def test_factor_resolution_public_contract_keeps_event_source_availability_keyword():
    signature = inspect.signature(resolve_factor_states)
    assert "event_source_available" in signature.parameters
    assert signature.parameters["event_source_available"].kind is inspect.Parameter.KEYWORD_ONLY


def test_factor_resolution_logical_count_and_physical_scope_rows_are_distinct():
    states, summary = resolve_factor_states(
        [], [], datetime(2026, 9, 10, tzinfo=timezone.utc), event_source_available=True
    )
    assert summary.total_factors == 27
    assert summary.state_rows == len(states)
    assert summary.state_rows > summary.total_factors
    assert any(state.asset_id == "CSI300" for state in states)
