from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from widegold.repositories.factory import repository
from widegold.schemas.query import EvidenceDocument, EventDetailResponse, EventListItem, EventListResponse

router = APIRouter(tags=["events"])


def _published_run_id() -> UUID:
    snapshot = repository().latest_published()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No published snapshot is available yet.")
    return snapshot.analysis_run_id


@router.get("/events", response_model=EventListResponse)
def list_events(
    factor_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    repo = repository()
    run_id = _published_run_id()
    events = repo.get_events(run_id)
    rows: list[EventListItem] = []
    for event in sorted(events, key=lambda x: x.published_at, reverse=True):
        factor_ids = [mapping.factor_id for mapping in event.factors]
        if factor_id and factor_id not in factor_ids:
            continue
        if event_type and event.event_type != event_type:
            continue
        rows.append(EventListItem(
            event_id=event.event_id,
            analysis_run_id=run_id,
            canonical_title=event.canonical_title,
            event_type=event.event_type,
            published_at=event.published_at,
            source_tier=event.source_tier.value,
            strength=event.strength,
            confidence=event.confidence,
            factor_ids=factor_ids,
            reason_tags=event.reason_tags,
        ))
        if len(rows) >= limit:
            break
    return EventListResponse(analysis_run_id=run_id, items=rows)


@router.get("/events/{event_id}", response_model=EventDetailResponse)
def event_detail(event_id: UUID):
    repo = repository()
    run_id = _published_run_id()
    event = next((row for row in repo.get_events(run_id) if row.event_id == event_id), None)
    if event is None:
        # Public API deliberately exposes only events from the current published snapshot.
        raise HTTPException(status_code=404, detail="Event not found in the current published analysis")
    documents = repo.get_documents(event.evidence_document_ids)
    evidence = [EvidenceDocument(
        document_id=doc.document_id,
        source_id=doc.source_id,
        source_tier=doc.source_tier.value,
        title=doc.title,
        url=doc.url,
        published_at=doc.published_at,
        retrieved_at=doc.retrieved_at,
        excerpt=(doc.content[:500] + "…") if len(doc.content) > 500 else doc.content,
    ) for doc in documents]
    return EventDetailResponse(analysis_run_id=run_id, event=event, evidence=evidence)
