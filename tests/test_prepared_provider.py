from datetime import datetime, timezone

from widegold.data.providers.prepared import PreparedFactorInputProvider
from widegold.domain.enums import DataStatus
from widegold.schemas.resilience import FactorInput


def test_prepared_provider_keeps_scoped_factor_inputs():
    as_of = datetime(2026, 9, 10, 18, 10, tzinfo=timezone.utc)
    rows = [
        FactorInput(
            factor_id="EQ14_TREND_MOMENTUM",
            asset_id="CSI300",
            value=25,
            observed_at=as_of,
            status=DataStatus.VALID,
            reliability=0.8,
        )
    ]
    provider = PreparedFactorInputProvider(as_of=as_of, factor_inputs=rows)
    assert provider.factor_inputs()[0].asset_id == "CSI300"
    assert provider.load().news == []
