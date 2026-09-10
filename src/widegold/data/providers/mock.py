import json
from datetime import datetime

from widegold.domain.enums import DataStatus, SourceTier
from widegold.schemas.events import NewsDocument
from widegold.schemas.providers import MockMarketBundle
from widegold.schemas.resilience import FactorInput
from widegold.settings.config import FIXTURE_DIR, asset_config, factor_config


class MockAnalysisDataProvider:
    def __init__(self, fixture_name: str = "mock_day.json", fail_factors: set[str] | None = None) -> None:
        self.fixture_name = fixture_name
        self.fail_factors = fail_factors or set()

    def load(self) -> MockMarketBundle:
        payload = json.loads((FIXTURE_DIR / self.fixture_name).read_text(encoding="utf-8"))
        as_of = datetime.fromisoformat(payload["as_of"])
        docs = []
        for item in payload["news"]:
            published_at = datetime.fromisoformat(item["published_at"])
            docs.append(NewsDocument(
                source_id=item["source_id"],
                source_tier=SourceTier(item["source_tier"]),
                title=item["title"],
                content=item["content"],
                published_at=published_at,
                retrieved_at=as_of,
            ))
        return MockMarketBundle(
            analysis_date=payload["analysis_date"],
            as_of=as_of,
            base_factor_states=payload["base_factor_states"],
            news=docs,
        )

    def factor_inputs(self) -> list[FactorInput]:
        bundle = self.load()
        meta = {item["id"]: item for item in factor_config()["factors"]}
        assets = [item for item in asset_config().get("assets", []) if item.get("active", True)]
        inputs: list[FactorInput] = []
        for factor_id, value in bundle.base_factor_states.items():
            factor_meta = meta[factor_id]
            targets: list[str | None]
            if factor_meta.get("scope", "global") == "asset":
                targets = [
                    item["id"] for item in assets
                    if item.get("family") == factor_meta.get("family")
                ]
            else:
                targets = [None]

            for asset_id in targets:
                if factor_id in self.fail_factors:
                    inputs.append(FactorInput(
                        factor_id=factor_id,
                        asset_id=asset_id,
                        value=None,
                        observed_at=bundle.as_of,
                        status=DataStatus.UNAVAILABLE,
                        reliability=0.0,
                        source_ids=["MOCK_FAILED"],
                        warnings=["Simulated provider failure"],
                    ))
                    continue
                inputs.append(FactorInput(
                    factor_id=factor_id,
                    asset_id=asset_id,
                    value=float(value),
                    observed_at=bundle.as_of,
                    status=DataStatus.VALID,
                    reliability=float(factor_meta["reliability"]),
                    source_ids=["MOCK_FIXTURE"],
                ))
        return inputs

