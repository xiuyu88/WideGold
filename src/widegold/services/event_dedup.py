from __future__ import annotations

import re
from difflib import SequenceMatcher

from widegold.domain.enums import EventVerificationStatus, SourceTier
from widegold.schemas.events import StructuredEvent

_PUNCT_RE = re.compile(r"[\s\u3000，。；：、！？,.!?;:'\"“”‘’（）()【】\[\]<>《》—_\-/]+")
_SOURCE_RANK = {SourceTier.S: 5, SourceTier.A: 4, SourceTier.B: 3, SourceTier.C: 2, SourceTier.D: 1}
_VERIFY_RANK = {
    EventVerificationStatus.VERIFIED: 4,
    EventVerificationStatus.PARTIALLY_VERIFIED: 3,
    EventVerificationStatus.UNVERIFIED: 2,
    EventVerificationStatus.REJECTED: 1,
}


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", text.lower())


def _sim(left: str, right: str) -> float:
    a, b = _norm(left), _norm(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _factor_signature(event: StructuredEvent) -> tuple[str, ...]:
    return tuple(sorted(mapping.factor_id for mapping in event.factors))


def _best(left: StructuredEvent, right: StructuredEvent) -> StructuredEvent:
    def score(event: StructuredEvent):
        return (
            _VERIFY_RANK.get(event.verification_status, 0),
            _SOURCE_RANK.get(event.source_tier, 0),
            event.confidence,
            event.strength,
        )
    primary, secondary = (left, right) if score(left) >= score(right) else (right, left)
    return primary.model_copy(update={
        "published_at": min(left.published_at, right.published_at),
        "effective_at": max(
            [x for x in (left.effective_at, right.effective_at) if x is not None],
            default=primary.effective_at,
        ),
        "evidence_document_ids": list(dict.fromkeys([
            *primary.evidence_document_ids,
            *secondary.evidence_document_ids,
        ])),
        "model_execution_ids": list(dict.fromkeys([
            *primary.model_execution_ids,
            *secondary.model_execution_ids,
        ])),
        "reason_tags": list(dict.fromkeys([*primary.reason_tags, *secondary.reason_tags])),
    })


def deduplicate_structured_events(
    events: list[StructuredEvent], *, similarity_threshold: float = 0.72, max_gap_hours: float = 12.0
) -> list[StructuredEvent]:
    """Conservatively merge duplicate event interpretations after the LLM graph.

    Events are merge candidates only if event type and factor signature are identical, their
    timestamps are close, and canonical titles are highly similar. The higher-quality event keeps
    its impact parameters; evidence/model references from both interpretations are retained. This
    prevents duplicate impact without averaging two LLM opinions into a stronger signal.
    """
    if len(events) < 2:
        return list(events)
    ordered = sorted(events, key=lambda e: (e.published_at, e.event_type, e.canonical_title))
    merged: list[StructuredEvent] = []
    for event in ordered:
        match_idx = None
        for idx, existing in enumerate(merged):
            if existing.event_type != event.event_type:
                continue
            if _factor_signature(existing) != _factor_signature(event):
                continue
            gap = abs((existing.published_at - event.published_at).total_seconds()) / 3600.0
            if gap > max_gap_hours:
                continue
            if _sim(existing.canonical_title, event.canonical_title) >= similarity_threshold:
                match_idx = idx
                break
        if match_idx is None:
            merged.append(event)
        else:
            merged[match_idx] = _best(merged[match_idx], event)
    return merged
