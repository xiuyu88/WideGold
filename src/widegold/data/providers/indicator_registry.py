from widegold.data.providers.akshare_derived import AkshareDerivedIndicatorProvider
from widegold.data.providers.akshare_provider import AkshareIndicatorProvider
from widegold.data.providers.fred import FredIndicatorProvider
from widegold.data.providers.nbs_industrial_profit import NbsIndustrialProfitProvider
from widegold.data.providers.external_bridge import ExternalIndicatorBridgeProvider
from widegold.settings.app import get_settings


def indicator_provider(name: str):
    settings = get_settings()
    if name == "fred":
        return FredIndicatorProvider(settings.fred_api_key)
    if name == "akshare":
        return AkshareIndicatorProvider()
    if name == "akshare_derived":
        return AkshareDerivedIndicatorProvider()
    if name == "nbs":
        return NbsIndustrialProfitProvider()
    if name == "external_bridge":
        if not settings.external_indicator_url:
            raise RuntimeError("WIDEGOLD_EXTERNAL_INDICATOR_URL is not configured")
        return ExternalIndicatorBridgeProvider(
            settings.external_indicator_url, settings.external_indicator_api_key
        )
    raise KeyError(f"Unknown indicator provider: {name}")
