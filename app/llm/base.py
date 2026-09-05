"""LLM 提供方抽象。

Agent Loop 只依赖这个接口;把 MockProvider 换成真实模型(OpenAI 兼容/国产/本地)
只需实现 chat() 返回相同结构即可,Loop 与工具代码无需改动。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class _PlannerSchema(BaseModel):
    """Planner 输入输出共用的严格 Schema 基类。"""
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class PlanStep(_PlannerSchema):
    """一次策略运行中的受控执行阶段。"""
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    goal: str = Field(..., min_length=1, max_length=160)
    allowed_tools: list[str] = Field(..., min_length=1, max_length=4)
    required_evidence: list[str] = Field(default_factory=list, max_length=6)
    exit_condition: str = Field(..., min_length=1, max_length=160)
    fallback: str = Field(..., min_length=1, max_length=160)
    status: Literal["pending", "running", "completed", "blocked", "skipped"] = "pending"

    @field_validator("allowed_tools", "required_evidence", mode="before")
    @classmethod
    def normalize_single_value_list(cls, value: Any) -> Any:
        """仅兼容单值字符串，避免低风险格式波动导致整轮降级。"""
        if isinstance(value, str):
            return [value]
        if value is None:
            return []
        return value

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class AgentPlan(_PlannerSchema):
    """Planner 产生的简短、可审计计划，而非模型完整思维链。"""
    goal: str = Field(..., min_length=1, max_length=160)
    decision_reason: str = Field(..., min_length=1, max_length=160)
    evidence_gap: list[str] = Field(default_factory=list, max_length=6)
    steps: list[PlanStep] = Field(..., min_length=1, max_length=3)
    replan_count: int = Field(default=0, ge=0, le=1)

    @field_validator("evidence_gap", mode="before")
    @classmethod
    def normalize_evidence_gap(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        if value is None:
            return []
        return value

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentPlan":
        return cls.model_validate(payload)


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
