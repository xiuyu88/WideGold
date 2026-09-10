from typing import TypedDict

from widegold.schemas.scores import AssetExplanation, AssetScore


class ExplanationState(TypedDict, total=False):
    asset_score: AssetScore
    explanation: AssetExplanation


def _explain(state: ExplanationState) -> ExplanationState:
    score = state["asset_score"]
    positives = score.top_positive[:3]
    negatives = score.top_negative[:3]
    summary = f"{score.asset_name}当前评分 {score.score:.1f}，状态为“{score.label}”。"
    direction_reason = (
        "主要由" + "、".join(positives) + "提供支持。" if positives else "当前没有明显的正向主导因子。"
    )
    if negatives:
        direction_reason += " 同时需关注" + "、".join(negatives) + "的压制。"
    biggest_risk = score.risk_flags[0] if score.risk_flags else "当前没有触发规则级高优先风险标记。"
    return {
        "explanation": AssetExplanation(
            asset_id=score.asset_id,
            summary=summary,
            direction_reason=direction_reason,
            top_positive=positives,
            top_negative=negatives,
            biggest_risk=biggest_risk,
            confidence_explanation=f"当前置信度 {score.confidence.confidence:.1f}%，数据覆盖与因子一致性共同决定该数值。",
        )
    }


class DirectExplanationGraph:
    def invoke(self, state: ExplanationState) -> ExplanationState:
        current = dict(state)
        current.update(_explain(current))
        return current  # type: ignore[return-value]


def build_explanation_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return DirectExplanationGraph()
    builder = StateGraph(ExplanationState)
    builder.add_node("explain", _explain)
    builder.add_edge(START, "explain")
    builder.add_edge("explain", END)
    return builder.compile()
