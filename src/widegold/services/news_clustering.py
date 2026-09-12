from __future__ import annotations

import re
from difflib import SequenceMatcher
from uuid import NAMESPACE_URL, uuid5

from widegold.domain.enums import SourceTier
from widegold.schemas.events import NewsCluster, NewsDocument
from widegold.settings.app import get_settings
from widegold.settings.config import news_config

_PUNCT_RE = re.compile(r"[\s\u3000，。；：、！？,.!?;:'\"“”‘’（）()【】\[\]<>《》—_\-/]+")
_TIER_RANK = {SourceTier.S: 5, SourceTier.A: 4, SourceTier.B: 3, SourceTier.C: 2, SourceTier.D: 1}


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub("", text.lower())


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _title_similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    aa, bb = _bigrams(a), _bigrams(b)
    union = aa | bb
    jaccard = len(aa & bb) / len(union) if union else 0.0
    # Sequence similarity is tolerant to reordered short phrases while bigram overlap prevents
    # unrelated generic financial headlines from being merged too aggressively.
    return 0.55 * seq + 0.45 * jaccard


def _cluster_id(documents: list[NewsDocument]) -> object:
    stable_key = "|".join(sorted(str(doc.document_id) for doc in documents))
    return uuid5(NAMESPACE_URL, f"widegold:news-cluster:{stable_key}")


def _canonical_document(documents: list[NewsDocument]) -> NewsDocument:
    # Prefer higher-quality sources, then longer/more informative title, then fresher timestamp.
    return max(
        documents,
        key=lambda d: (_TIER_RANK.get(d.source_tier, 0), len(d.title), d.published_at),
    )


def cluster_news_documents(documents: list[NewsDocument]) -> list[NewsCluster]:
    """Deterministically cluster near-duplicate headlines before any LLM call.

    The daily discovery set is intentionally small (<= configured cap), so an O(n²) greedy
    algorithm is easier to audit than an embedding dependency. Clustering is a *cost and duplicate
    impact guard*, not semantic event reasoning; ambiguous cases remain separate and are handled by
    the Event Intelligence graph.
    """
    if not documents:
        return []
    full_cfg = news_config()
    cfg = full_cfg.get("clustering", {})
    if not cfg.get("enabled", True):
        return _apply_cluster_budget([
            NewsCluster(
                cluster_id=_cluster_id([doc]),
                canonical_title=doc.title,
                documents=[doc],
                first_seen_at=doc.published_at,
                last_seen_at=doc.published_at,
            )
            for doc in documents
        ], full_cfg)

    threshold = float(cfg.get("similarity_threshold", 0.62))
    max_gap_seconds = float(cfg.get("max_time_gap_hours", 18)) * 3600.0
    ordered = sorted(documents, key=lambda d: (d.published_at, d.title, str(d.document_id)))
    groups: list[list[NewsDocument]] = []

    for doc in ordered:
        best_index = None
        best_score = 0.0
        for idx, group in enumerate(groups):
            canonical = _canonical_document(group)
            gap = abs((doc.published_at - canonical.published_at).total_seconds())
            if gap > max_gap_seconds:
                continue
            score = _title_similarity(doc.title, canonical.title)
            if score >= threshold and score > best_score:
                best_index = idx
                best_score = score
        if best_index is None:
            groups.append([doc])
        else:
            groups[best_index].append(doc)

    clusters = []
    for group in groups:
        canonical = _canonical_document(group)
        clusters.append(NewsCluster(
            cluster_id=_cluster_id(group),
            canonical_title=canonical.title,
            documents=sorted(group, key=lambda d: (d.published_at, str(d.document_id))),
            first_seen_at=min(d.published_at for d in group),
            last_seen_at=max(d.published_at for d in group),
        ))
    clusters.sort(key=lambda c: (c.first_seen_at, c.canonical_title))
    return _apply_cluster_budget(clusters, full_cfg)


def _cluster_priority(cluster: NewsCluster) -> tuple:
    """Rank clusters by expected event value before spending LLM calls on them."""
    canonical = _canonical_document(cluster.documents)
    return (
        len(cluster.documents),                 # corroborated by more sources
        _TIER_RANK.get(canonical.source_tier, 0),
        cluster.last_seen_at,                   # fresher first
    )


def _apply_cluster_budget(clusters: list[NewsCluster], cfg: dict) -> list[NewsCluster]:
    """Cap how many clusters may reach the Event Intelligence graph.

    Every surviving cluster costs at least one LLM call per graph node, so an uncapped news set
    turns directly into an uncapped bill.  ``news.yaml:max_clusters`` is the tuning knob; the
    environment setting is the ceiling that still applies when DB runtime config is stale.
    """
    ceiling = int(get_settings().event_max_clusters or 0)
    configured = int(cfg.get("max_clusters") or 0)
    limits = [value for value in (ceiling, configured) if value > 0]
    if not limits:
        return clusters
    budget = min(limits)
    if len(clusters) <= budget:
        return clusters
    kept = sorted(clusters, key=_cluster_priority, reverse=True)[:budget]
    kept.sort(key=lambda c: (c.first_seen_at, c.canonical_title))
    return kept
