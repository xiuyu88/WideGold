from widegold.data.providers.disabled import DisabledAnalysisDataProvider
from widegold.data.providers.mock import MockAnalysisDataProvider
from widegold.data.providers.normalized_feed import NormalizedFeedProvider
from widegold.data.providers.raw_indicators import RawIndicatorAnalysisProvider
from widegold.settings.app import get_settings


def analysis_provider():
    settings = get_settings()
    mode = settings.data_mode.lower()
    if mode == "mock":
        return MockAnalysisDataProvider()
    if mode == "normalized_feed":
        if not settings.factor_feed_url:
            raise RuntimeError("WIDEGOLD_FACTOR_FEED_URL is required for normalized_feed mode")
        return NormalizedFeedProvider(settings.factor_feed_url)
    if mode == "raw_indicators":
        return RawIndicatorAnalysisProvider()
    if mode == "disabled":
        return DisabledAnalysisDataProvider()
    raise RuntimeError(f"Unknown WIDEGOLD_DATA_MODE: {settings.data_mode}")
