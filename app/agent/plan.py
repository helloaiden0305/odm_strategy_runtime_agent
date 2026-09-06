"""轻量 Plan-Execute 运行时的计划结构、校验与安全默认计划。"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..llm.base import AgentPlan, PlanStep


_ALLOWED_STATUSES = {"pending", "running", "completed", "blocked", "skipped"}
_MAX_TEXT_LENGTH = 160


@dataclass
class PlanGuardResult:
    valid: bool
    reasons: list[str]


def build_safe_default_plan() -> AgentPlan:
    """Planner 无法产出合规 JSON 时使用的最小保守计划。"""
    return AgentPlan(
        goal="先确认可复用的专家策略，再决定是否需要补充证据或升级。",
        decision_reason="规划结果不可用，先执行受控的策略召回与证据判断。",
        evidence_gap=["问题现象", "复现条件", "版本和关键日志"],
        steps=[
            PlanStep(
                id="recall_strategy",
                goal="确认已有专家策略是否可复用。",
                allowed_tools=["recall_troubleshooting_strategy"],
                required_evidence=["策略样本召回结果"],
                exit_condition="已获得策略样本结果或确认未命中。",
                fallback="策略未命中时进入补充证据或升级判断。",
            ),
            PlanStep(
                id="collect_evidence",
                goal="补齐测试规范或历史案例证据。",
                allowed_tools=["test_sop_search", "defect_case_search"],
                exit_condition="已取得可用证据或明确未命中。",
                fallback="保留待补充信息并进入升级判断。",
            ),
            PlanStep(
                id="conclude_or_escalate",
                goal="基于证据给出下一步建议或升级专家。",
                allowed_tools=["escalate_to_expert"],
                exit_condition="输出受控结论、追问或完成升级。",
                fallback="明确说明待补充信息。",
            ),
        ],
    )


def validate_plan(plan: AgentPlan, available_tools: set[str]) -> PlanGuardResult:
    """只审核计划结构与执行边界，不承担业务语义裁决。"""
    reasons: list[str] = []
    if not plan.goal.strip():
        reasons.append("缺少计划目标")
    if not plan.decision_reason.strip():
        reasons.append("缺少决策摘要")
    if len(plan.steps) < 1 or len(plan.steps) > config.MAX_PLAN_STEPS:
        reasons.append(f"计划步骤数量必须为 1 到 {config.MAX_PLAN_STEPS}")
    if plan.steps and "recall_troubleshooting_strategy" not in plan.steps[0].allowed_tools:
        reasons.append("首步必须允许策略召回工具")

    seen_ids: set[str] = set()
    for step in plan.steps:
        if not step.id.strip() or step.id in seen_ids:
            reasons.append("步骤 id 不能为空且不能重复")
        seen_ids.add(step.id)
        if not step.goal.strip() or not step.exit_condition.strip() or not step.fallback.strip():
            reasons.append(f"步骤 {step.id or '?'} 缺少目标、退出条件或兜底路径")
        if not step.allowed_tools:
            reasons.append(f"步骤 {step.id or '?'} 未声明允许工具")
        unknown = sorted(set(step.allowed_tools) - available_tools)
        if unknown:
            reasons.append(f"步骤 {step.id or '?'} 包含未知工具: {', '.join(unknown)}")
        if step.status not in _ALLOWED_STATUSES:
            reasons.append(f"步骤 {step.id or '?'} 状态不合法")
        fields = [step.id, step.goal, step.exit_condition, step.fallback, *step.required_evidence]
        if any(len(value) > _MAX_TEXT_LENGTH for value in fields):
            reasons.append(f"步骤 {step.id or '?'} 存在过长字段")

    if any(len(value) > _MAX_TEXT_LENGTH for value in [plan.goal, plan.decision_reason, *plan.evidence_gap]):
        reasons.append("计划存在过长字段")
    if plan.replan_count < 0:
        reasons.append("重规划次数不合法")
    return PlanGuardResult(valid=not reasons, reasons=reasons)
