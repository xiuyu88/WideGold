from datetime import datetime, timedelta, timezone

from widegold.domain.enums import SourceTier
from widegold.schemas.events import NewsDocument
from widegold.services.news_clustering import cluster_news_documents


def _doc(title: str, hours: int = 0, tier=SourceTier.C):
    base = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
    return NewsDocument(
        source_id="TEST",
        source_tier=tier,
        title=title,
        content=title,
        published_at=base + timedelta(hours=hours),
        retrieved_at=base + timedelta(hours=hours, minutes=1),
    )


def test_near_duplicate_financial_headlines_cluster_before_llm():
    docs = [
        _doc("美联储释放偏鸽信号 美债实际利率回落", 0),
        _doc("美联储释放偏鸽信号：美债实际利率回落", 1, SourceTier.B),
        _doc("中国制造业PMI公布 市场关注增长动能", 2),
    ]
    clusters = cluster_news_documents(docs)
    assert len(clusters) == 2
    duplicate_cluster = next(x for x in clusters if len(x.documents) == 2)
    assert duplicate_cluster.canonical_title == docs[1].title


def test_cluster_id_is_deterministic_for_same_document_set():
    docs = [_doc("黄金价格受到美元与实际利率影响", 0), _doc("黄金价格受到美元和实际利率影响", 1)]
    first = cluster_news_documents(docs)
    second = cluster_news_documents(list(reversed(docs)))
    assert [x.cluster_id for x in first] == [x.cluster_id for x in second]
