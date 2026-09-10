from widegold.llm.router import ModelRouter


def test_expert_review_prefers_cost_controlled_deepseek_flash():
    route = ModelRouter().route("expert_review")
    assert [item.alias for item in route] == ["deepseek_high", "gpt_expert"]
    assert route[0].model == "deepseek-v4-flash"
