from datetime import date, datetime, timezone

from widegold.data.providers.fred import FredIndicatorProvider
from widegold.schemas.indicators import IndicatorFetchRequest


def test_fred_passes_registered_units_without_overriding_point_in_time(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"observations": [{"date": "2026-08-01", "value": "2.7", "realtime_start": "2026-09-10", "realtime_end": "2026-09-10"}]}

    def fake_get(url, *, params, timeout):
        captured.update(params)
        return Response()

    monkeypatch.setattr("widegold.data.providers.fred.httpx.get", fake_get)
    provider = FredIndicatorProvider("key")
    request = IndicatorFetchRequest(
        indicator_id="US_CPI_YOY",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 9, 10),
        as_of=datetime(2026, 9, 10, 18, 10, tzinfo=timezone.utc),
    )
    result = provider.fetch(request, {"source_id": "FRED", "params": {"series_id": "CPIAUCSL", "units": "pc1"}})
    assert captured["units"] == "pc1"
    assert captured["realtime_start"] == "2026-09-10"
    assert captured["realtime_end"] == "2026-09-10"
    assert result.observations[0].value == 2.7
