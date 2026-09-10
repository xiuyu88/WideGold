from datetime import date, datetime, timezone

import httpx

from widegold.data.providers.external_bridge import (
    EXTERNAL_BRIDGE_CONTRACT_VERSION,
    ExternalIndicatorBridgeProvider,
)
from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest

AS_OF = datetime(2026, 9, 10, 10, 10, tzinfo=timezone.utc)


def test_external_bridge_normalizes_observations_and_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fetch"
        assert request.headers["authorization"] == "Bearer secret"
        payload = __import__("json").loads(request.content)
        assert payload["contract_version"] == EXTERNAL_BRIDGE_CONTRACT_VERSION
        assert payload["capability"] == "china_money.dr007"
        assert payload["indicator_id"] == "CN_DR007"
        return httpx.Response(200, json={
            "contract_version": EXTERNAL_BRIDGE_CONTRACT_VERSION,
            "capability": "china_money.dr007",
            "source_id": "MCP_GATEWAY",
            "status": "VALID",
            "observations": [
                {
                    "observation_date": "2026-09-10",
                    "release_ts": "2026-09-10T09:00:00+00:00",
                    "value": 1.52,
                    "definition_version": "DR007_V1",
                    "metadata": {"benchmark": "DR007"},
                }
            ],
        })

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://bridge")
    provider = ExternalIndicatorBridgeProvider("http://bridge", "secret", client=client)
    result = provider.fetch(
        IndicatorFetchRequest(
            indicator_id="CN_DR007",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            as_of=AS_OF,
        ),
        {
            "source_id": "CHINAMONEY",
            "external_params": {"capability": "china_money.dr007"},
        },
    )
    assert result.status == DataStatus.VALID
    assert len(result.observations) == 1
    assert result.observations[0].value == 1.52
    assert result.observations[0].source_id == "MCP_GATEWAY"


def test_external_bridge_empty_valid_payload_is_downgraded_to_unavailable():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "contract_version": EXTERNAL_BRIDGE_CONTRACT_VERSION,
                "capability": "equity_index.earnings_revision",
                "source_id": "VENDOR",
                "status": "VALID",
                "observations": [],
            })
        ),
        base_url="http://bridge",
    )
    provider = ExternalIndicatorBridgeProvider("http://bridge", client=client)
    result = provider.fetch(
        IndicatorFetchRequest(
            indicator_id="CN_INDEX_EARNINGS_REV",
            asset_id="CSI300",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            as_of=AS_OF,
        ),
        {
            "source_id": "LICENSED_OR_AGGREGATED",
            "external_params": {"capability": "equity_index.earnings_revision"},
        },
    )
    assert result.status == DataStatus.UNAVAILABLE
    assert "external_bridge_empty" in result.warnings


def test_external_bridge_rejects_missing_release_timestamp():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "contract_version": EXTERNAL_BRIDGE_CONTRACT_VERSION,
                "capability": "china_money.dr007",
                "source_id": "BAD_GATEWAY",
                "status": "VALID",
                "observations": [{"observation_date": "2026-09-10", "value": 1.5}],
            })
        ),
        base_url="http://bridge",
    )
    result = ExternalIndicatorBridgeProvider("http://bridge", client=client).fetch(
        IndicatorFetchRequest(
            indicator_id="CN_DR007",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            as_of=AS_OF,
        ),
        {"source_id": "CHINAMONEY", "external_params": {"capability": "china_money.dr007"}},
    )
    assert result.status == DataStatus.UNAVAILABLE
    assert result.observations == []
    assert any(item.startswith("external_bridge_contract_invalid:") for item in result.warnings)


def test_external_bridge_rejects_capability_mismatch():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "contract_version": EXTERNAL_BRIDGE_CONTRACT_VERSION,
                "capability": "wrong.capability",
                "source_id": "BAD_GATEWAY",
                "status": "VALID",
                "observations": [],
            })
        ),
        base_url="http://bridge",
    )
    result = ExternalIndicatorBridgeProvider("http://bridge", client=client).fetch(
        IndicatorFetchRequest(
            indicator_id="CN_DR007",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            as_of=AS_OF,
        ),
        {"source_id": "CHINAMONEY", "external_params": {"capability": "china_money.dr007"}},
    )
    assert result.status == DataStatus.UNAVAILABLE
    assert result.observations == []
    assert "external_bridge_capability_mismatch:wrong.capability" in result.warnings
