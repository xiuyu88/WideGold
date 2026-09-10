from datetime import datetime
from zoneinfo import ZoneInfo

from widegold.domain.enums import DataStatus
from widegold.schemas.providers import MockMarketBundle
from widegold.schemas.resilience import FactorInput
from widegold.settings.config import factor_config


class DisabledAnalysisDataProvider:
    provider_name = "disabled"

    def load(self) -> MockMarketBundle:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        return MockMarketBundle(
            analysis_date=now.date().isoformat(),
            as_of=now,
            base_factor_states={},
            news=[],
            news_status=DataStatus.UNAVAILABLE,
            news_warnings=["Real news provider is not configured."],
        )

    def factor_inputs(self) -> list[FactorInput]:
        now = self.load().as_of
        return [
            FactorInput(
                factor_id=item["id"],
                value=None,
                observed_at=now,
                status=DataStatus.UNAVAILABLE,
                reliability=0.0,
                warnings=["Real data provider is not configured."],
            )
            for item in factor_config()["factors"]
        ]
