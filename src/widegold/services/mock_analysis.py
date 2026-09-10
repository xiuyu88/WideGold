from widegold.data.providers.mock import MockAnalysisDataProvider
from widegold.domain.enums import PublishMode
from widegold.schemas.common import AnalysisRunRequest
from widegold.services.analysis import run_analysis


def run_mock_analysis(publish: bool = True):
    request = AnalysisRunRequest(
        publish_mode=PublishMode.EXPLICIT_PUBLISH if publish else PublishMode.PREVIEW_ONLY
    )
    return run_analysis(request, provider=MockAnalysisDataProvider())
