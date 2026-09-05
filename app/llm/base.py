"""LLM 提供方抽象。

Agent Loop 只依赖这个接口;把 MockProvider 换成真实模型(OpenAI 兼容/国产/本地)
只需实现 chat() 返回相同结构即可,Loop 与工具代码无需改动。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    """模型本步请求调用的单个工具。

    id:    本次调用的唯一标识,用于工具结果回填时与调用配对。
    name:  工具名。
    input: 工具入参。
    """
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMDecision:
    """一次模型决策的结果。

    type == "tool_call":  需要调用 tool_calls 中的工具(可一步并行多个)
    type == "final":      产出最终回复 content
    thought:              本步的思考(用于轨迹可视化)
    """
    type: str
    thought: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: Optional[str] = None


@dataclass
class PlanStep:
    """一次策略运行中的受控执行阶段。"""
    id: str
    goal: str
    allowed_tools: list[str]
    required_evidence: list[str] = field(default_factory=list)
    exit_condition: str = ""
    fallback: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "allowed_tools": list(self.allowed_tools),
            "required_evidence": list(self.required_evidence),
            "exit_condition": self.exit_condition,
            "fallback": self.fallback,
            "status": self.status,
        }


@dataclass
class AgentPlan:
    """Planner 产生的简短、可审计计划，而非模型完整思维链。"""
    goal: str
    decision_reason: str
    evidence_gap: list[str]
    steps: list[PlanStep]
    replan_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "decision_reason": self.decision_reason,
            "evidence_gap": list(self.evidence_gap),
            "steps": [step.to_dict() for step in self.steps],
            "replan_count": self.replan_count,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentPlan":
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError("steps 必须是数组")
        steps: list[PlanStep] = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                raise ValueError("每个步骤必须是对象")
            tools = raw.get("allowed_tools")
            gaps = raw.get("required_evidence", [])
            if not isinstance(tools, list) or not isinstance(gaps, list):
                raise ValueError("步骤工具和证据必须是数组")
            steps.append(PlanStep(
                id=str(raw.get("id", "")),
                goal=str(raw.get("goal", "")),
                allowed_tools=[str(item) for item in tools],
                required_evidence=[str(item) for item in gaps],
                exit_condition=str(raw.get("exit_condition", "")),
                fallback=str(raw.get("fallback", "")),
                status=str(raw.get("status", "pending")),
            ))
        gaps = payload.get("evidence_gap", [])
        if not isinstance(gaps, list):
            raise ValueError("evidence_gap 必须是数组")
        return cls(
            goal=str(payload.get("goal", "")),
            decision_reason=str(payload.get("decision_reason", "")),
            evidence_gap=[str(item) for item in gaps],
            steps=steps,
            replan_count=int(payload.get("replan_count", 0)),
        )


class LLMProvider(ABC):
    @abstractmethod
    def plan(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]]) -> AgentPlan:
        """生成一次运行的短计划；返回值必须可由代码进一步校验。"""
        raise NotImplementedError

    def replan(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               previous_plan: AgentPlan, reason: str) -> AgentPlan:
        """默认复用 Planner；提供方可按失败原因实现更精确的剩余步骤修订。"""
        return self.plan(messages, tools)

    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMDecision:
        """根据对话历史(含已执行的工具结果)与可用工具,决定下一步。"""
        raise NotImplementedError
