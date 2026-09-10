from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from widegold.domain.enums import SourceTier
from widegold.schemas.events import NewsDocument

CN_TZ = ZoneInfo("Asia/Shanghai")


def _parse_cn_datetime(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        parsed = pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(timezone.utc)


class AkshareGlobalNewsProvider:
    """Recent global financial flashes from AKShare/Eastmoney.

    The upstream is a news discovery source, not an official verification source, therefore
    documents are intentionally tagged SourceTier.C. Event verification/escalation happens in
    the Event Intelligence graph rather than in this provider.
    """

    provider_name = "akshare_global_em"

    def fetch(
        self,
        *,
        as_of: datetime,
        lookback_hours: int,
        max_documents: int,
        keywords: list[str],
        minimum_relevance_score: int = 1,
        source_id: str = "EASTMONEY_NEWS",
        source_tier: SourceTier = SourceTier.C,
    ) -> list[NewsDocument]:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - production dependency
            raise RuntimeError("akshare package is not installed") from exc

        frame = ak.stock_info_global_em()
        required = {"标题", "摘要", "发布时间", "链接"}
        if not required.issubset(set(frame.columns)):
            raise RuntimeError(f"stock_info_global_em columns changed: {list(frame.columns)}")

        cutoff = as_of.astimezone(timezone.utc)
        lower = cutoff - timedelta(hours=max(1, int(lookback_hours)))
        retrieved = datetime.now(timezone.utc)
        candidates: list[tuple[int, datetime, NewsDocument]] = []
        seen: set[tuple[str, str]] = set()

        for row in frame.itertuples(index=False):
            data = row._asdict() if hasattr(row, "_asdict") else {}
            # itertuples can sanitize Chinese labels; positional access is more reliable here.
            title = str(row[frame.columns.get_loc("标题")]).strip()
            summary = str(row[frame.columns.get_loc("摘要")]).strip()
            published = _parse_cn_datetime(row[frame.columns.get_loc("发布时间")])
            url = str(row[frame.columns.get_loc("链接")]).strip() or None
            if not title or title.lower() == "nan" or published is None:
                continue
            if published < lower or published > cutoff:
                continue
            text = f"{title}\n{summary}"
            score = sum(1 for keyword in keywords if keyword and keyword.lower() in text.lower())
            if score < minimum_relevance_score:
                continue
            dedup_key = (title, url or "")
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            candidates.append((
                score,
                published,
                NewsDocument(
                    source_id=source_id,
                    source_tier=source_tier,
                    title=title,
                    content=summary if summary and summary.lower() != "nan" else title,
                    published_at=published,
                    retrieved_at=retrieved,
                    url=url,
                    language="zh-CN",
                ),
            ))

        # Prefer higher relevance and fresher documents, then cap the LLM workload.
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in candidates[: max(1, int(max_documents))]]
