import json
from pathlib import Path

from widegold.schemas.indicators import IndicatorObservation
from widegold.services.indicator_ingest import validate_indicator_observations


def test_all_declared_external_contract_examples_validate():
    payload = json.loads(Path("fixtures/external_indicator_contract_examples.json").read_text())
    observations = [IndicatorObservation.model_validate(item) for item in payload]
    specs = validate_indicator_observations(observations)

    external = [item for item in observations if specs[item.indicator_id]["provider"] == "external"]
    nbs = next(item for item in observations if item.indicator_id == "CN_INDUSTRIAL_PROFIT_YOY")
    assert len(external) == 5
    assert nbs.metadata["aggregation_scope"] == "ytd"
