import json
import sys

from widegold.repositories.factory import repository
from widegold.services.machine_output import build_machine_analysis_envelope
from widegold.services.mock_analysis import run_mock_analysis


def main() -> None:
    """Run the deterministic Mock pipeline and emit exactly one JSON document to stdout.

    Human diagnostics belong on stderr/logging. Keeping stdout machine-only makes this command
    safe for CI, release gates, shell pipelines, and admin automation.
    """
    snapshot = run_mock_analysis(publish=True)
    states = repository().get_factor_states(snapshot.analysis_run_id)
    envelope = build_machine_analysis_envelope(snapshot, factor_states=states)
    sys.stdout.write(
        json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        + "\n"
    )


if __name__ == "__main__":
    main()
