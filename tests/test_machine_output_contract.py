import json

from widegold.mock_runner import main
from widegold.repositories.memory import reset_memory_repository
from widegold.services.mock_analysis import run_mock_analysis
from widegold.services.machine_output import build_machine_analysis_envelope
from widegold.repositories.factory import repository


def setup_function():
    reset_memory_repository()


def test_machine_envelope_contains_stable_contract_and_physical_factor_states():
    snapshot = run_mock_analysis(publish=True)
    states = repository().get_factor_states(snapshot.analysis_run_id)
    envelope = build_machine_analysis_envelope(snapshot, factor_states=states)

    assert envelope.contract_version == "widegold.analysis.v1"
    assert envelope.analysis_run_id == snapshot.analysis_run_id
    assert len(envelope.assets) == 7
    assert len(envelope.factor_states) == 42
    assert envelope.factor_resolution["total_factors"] == 27
    assert envelope.quality_gate


def test_mock_runner_stdout_is_one_parseable_json_document(capsys):
    main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["contract_version"] == "widegold.analysis.v1"
    assert payload["status"] == "PUBLISHED"
    assert payload["published"] is True
    assert len(payload["assets"]) == 7
    assert len(payload["factor_states"]) == 42
    assert captured.out.count("\n") == 1
