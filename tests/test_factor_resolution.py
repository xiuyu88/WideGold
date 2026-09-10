

def test_event_only_factor_is_unavailable_when_news_source_failed():
    from datetime import datetime, timezone
    from widegold.engine.factor_resolution import resolve_factor_states

    states, _ = resolve_factor_states([], [], datetime(2026, 9, 10, tzinfo=timezone.utc), event_source_available=False)
    policy = next(x for x in states if x.factor_id == "EQ08_POLICY_SUPPORT")
    fed = next(x for x in states if x.factor_id == "G03_FED_POLICY_SURPRISE")
    assert policy.status.value == "UNAVAILABLE"
    assert fed.status.value == "UNAVAILABLE"
    assert policy.coverage == 0.0


def test_event_only_factor_can_be_neutral_after_successful_news_scan():
    from datetime import datetime, timezone
    from widegold.engine.factor_resolution import resolve_factor_states

    states, _ = resolve_factor_states([], [], datetime(2026, 9, 10, tzinfo=timezone.utc), event_source_available=True)
    policy = next(x for x in states if x.factor_id == "EQ08_POLICY_SUPPORT")
    assert policy.status.value == "VALID"
    assert policy.state == 0.0
