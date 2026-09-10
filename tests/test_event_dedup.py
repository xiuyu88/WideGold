from datetime import datetime, timedelta, timezone
from uuid import uuid4

from widegold.domain.enums import EventVerificationStatus, Horizon, SourceTier
from widegold.schemas.events import FactorMapping, StructuredEvent
from widegold.services.event_dedup import deduplicate_structured_events


def _event(title: str, *, tier=SourceTier.C, hours=0):
    base = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
    return StructuredEvent(
        event_type="FED_POLICY",
        canonical_title=title,
        published_at=base + timedelta(hours=hours),
        verification_status=EventVerificationStatus.VERIFIED,
        source_tier=tier,
        factors=[FactorMapping(factor_id="G03_FED_POLICY_SURPRISE", direction=1, mapping_confidence=0.9)],
        strength=4,
        confidence=0.85,
        novelty=0.7,
        priced_in=0.4,
        implementation=1.0,
        horizon=Horizon.TACTICAL,
        half_life_days=2,
        evidence_document_ids=[uuid4()],
    )


def test_duplicate_structured_events_merge_without_strength_amplification():
    low = _event("美联储释放偏鸽信号 降息预期升温", tier=SourceTier.C)
    high = _event("美联储释放偏鸽信号：降息预期升温", tier=SourceTier.A, hours=1)
    high = high.model_copy(update={"confidence": 0.93, "strength": 4})
    rows = deduplicate_structured_events([low, high])
    assert len(rows) == 1
    assert rows[0].source_tier == SourceTier.A
    assert rows[0].strength == 4
    assert len(rows[0].evidence_document_ids) == 2


def test_distinct_events_with_different_factor_signatures_do_not_merge():
    first = _event("美联储释放偏鸽信号")
    second = _event("美联储释放偏鸽信号", hours=1).model_copy(update={
        "factors": [FactorMapping(factor_id="G02_USD", direction=1, mapping_confidence=0.8)]
    })
    assert len(deduplicate_structured_events([first, second])) == 2
