from widegold.services.mock_analysis import run_mock_analysis


def test_mock_pipeline_returns_all_assets():
    snapshot = run_mock_analysis(publish=True)
    assert snapshot.status == "PUBLISHED"
    assert snapshot.published is True
    assert len(snapshot.assets) == 7
    assert {a.asset_id for a in snapshot.assets} == {
        "CSI300", "CSI_A500", "CSI500", "CSI1000", "CHINEXT", "STAR50", "RMB_GOLD"
    }
    assert len(snapshot.top_events) == 3


def test_all_scores_are_bounded_and_explainable():
    snapshot = run_mock_analysis(publish=True)
    for asset in snapshot.assets:
        assert 0 <= asset.score <= 100
        assert 0 <= asset.confidence.confidence <= 100
        assert asset.contributions
        assert asset.asset_id in snapshot.explanations
