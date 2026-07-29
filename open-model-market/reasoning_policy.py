"""Generate concise inference parameters without imposing an output-token ceiling.

The production request deliberately omits ``max_tokens``,
``max_completion_tokens`` and ``reasoning.max_tokens``. Models remain subject to
their own provider/model limits, while low reasoning effort, low verbosity and
compact prompts encourage short complete answers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict

from model_market import ModelInfo, RunConfig, SelectedExpert, SelectedJudge, TaskProfile


@dataclass(frozen=True)
class InferencePlan:
    effort: str
    max_tokens: int
    reasoning_tokens: int
    temperature: float
    reasoning_supported: bool
    external_tools_allowed: bool
    rationale: tuple[str, ...]

    def evidence(self) -> Dict[str, Any]:
        data = asdict(self)
        provider_limit = data.pop("max_tokens")
        data["provider_max_completion_tokens"] = provider_limit
        data["output_token_policy"] = "provider-model-limit-only"
        data["request_token_ceiling_sent"] = False
        data["reasoning_token_ceiling_sent"] = False
        return data


def _token_cap(requested: int, model: ModelInfo) -> int:
    """Compatibility helper used only for cost/evidence estimates, never requests."""
    del requested
    return max(0, int(model.max_completion_tokens))


def _supports_bounded_reasoning(model: ModelInfo) -> bool:
    return "reasoning" in model.supported_parameters and bool(model.reasoning.get("supports_max_tokens"))


def expert_inference_plan(
    run: RunConfig,
    profile: TaskProfile,
    expert: SelectedExpert,
    model: ModelInfo,
) -> InferencePlan:
    del run
    temperature = 0.12 if expert.seat_key == "core" else 0.08 if expert.seat_key == "cross" else 0.14
    return InferencePlan(
        effort="low",
        max_tokens=max(0, int(model.max_completion_tokens)),
        reasoning_tokens=0,
        temperature=temperature,
        reasoning_supported="reasoning" in model.supported_parameters,
        external_tools_allowed=False,
        rationale=(
            f"任务复杂度={profile.complexity}",
            f"固定席位={expert.function}",
            "不发送人为输出token上限",
            "low reasoning effort与low verbosity鼓励简短完整正文",
        ),
    )


def judge_inference_plan(
    run: RunConfig,
    profile: TaskProfile,
    judge: SelectedJudge,
    model: ModelInfo,
) -> InferencePlan:
    del run
    return InferencePlan(
        effort="low",
        max_tokens=max(0, int(model.max_completion_tokens)),
        reasoning_tokens=0,
        temperature=0.06,
        reasoning_supported="reasoning" in model.supported_parameters,
        external_tools_allowed=False,
        rationale=(
            f"任务复杂度={profile.complexity}",
            f"裁决职业={judge.profession}",
            "禁止外部工具",
            "不发送人为输出token上限",
            "完整性优先并要求合并重复信息、尽量缩短报告",
        ),
    )


def apply_plan(payload: Dict[str, Any], plan: InferencePlan, model: ModelInfo) -> Dict[str, Any]:
    """Apply soft concision controls while removing all request token ceilings."""
    payload.pop("max_tokens", None)
    payload.pop("max_completion_tokens", None)

    supported = set(model.supported_parameters)
    if "temperature" in supported:
        payload["temperature"] = plan.temperature
    else:
        payload.pop("temperature", None)

    if plan.reasoning_supported:
        payload["reasoning"] = {"exclude": True, "effort": "low"}
    else:
        payload.pop("reasoning", None)

    if "verbosity" in supported:
        payload["verbosity"] = "low"
    else:
        payload.pop("verbosity", None)
    return payload
