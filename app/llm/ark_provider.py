"""豆包(火山引擎 ARK)真实 LLM 提供方。

ARK 提供 OpenAI 兼容接口,这里用 openai SDK 调用,并把 Agent Loop 内部的
消息/工具格式与 OpenAI 的 function calling 格式互相转换。

Loop 与工具代码完全不用改动:它只依赖 LLMProvider.chat() 返回 LLMDecision。
"""
from __future__ import annotations
import json
from typing import Any

from openai import OpenAI

from .base import AgentPlan, LLMProvider, LLMDecision, ToolCall
from .. import config


class ArkLLMProvider(LLMProvider):
    def __init__(self) -> None:
        if not config.ARK_API_KEY:
            raise RuntimeError("未配置 ARK_API_KEY,请在 .env 中填写豆包密钥。")
        if not config.ARK_CHAT_MODEL:
            raise RuntimeError("未配置 ARK_CHAT_MODEL,请在 .env 中填写豆包模型/接入点 ID。")
        self.client = OpenAI(api_key=config.ARK_API_KEY, base_url=config.ARK_BASE_URL)
        self.model = config.ARK_CHAT_MODEL

    def plan(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]]) -> AgentPlan:
        tool_names = [tool["name"] for tool in tools]
        schema = AgentPlan.planner_json_schema()
        planner_prompt = (
            "你是 ODM 问题排查运行时的 Planner。只输出符合 JSON Schema 的 JSON 对象，不要 Markdown。"
            "decision_reason 是一两句审计摘要，不是完整思维链。首步 allowed_tools 必须包含 "
            "recall_troubleshooting_strategy；所有工具只能从以下名单选择："
            + ", ".join(tool_names)
            + "。按用户意图规划：只查询历史案例或通用参考时，安排策略召回、缺陷检索和收尾即可；"
            "此类查询命中案例后可以直接回答，不要因为缺少某台设备的版本、日志而升级专家。"
            "只有需要给出具体排查动作时才安排 SOP；只有用户明确要求升级，或高风险问题在已查询资料后"
            "仍无法给出安全下一步时才安排 escalate_to_expert。"
            + "。JSON Schema："
            + json.dumps(schema, ensure_ascii=False)
        )
        planner_messages = [{"role": "system", "content": planner_prompt}, *messages]
        raw = self._plan_completion(planner_messages, schema)
        try:
            return AgentPlan.model_validate_json(raw)
        except ValueError as exc:
            retry_messages = [
                *planner_messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": (
                    "刚才的输出未通过 Pydantic Schema 校验。请只输出修正后的 JSON 对象，"
                    "数组字段 evidence_gap、allowed_tools、required_evidence 必须使用 []；"
                    "不要使用 Markdown、解释或额外字段。校验错误：" + str(exc)
                )},
            ]
            return AgentPlan.model_validate_json(self._plan_completion(retry_messages, schema))

    def _plan_completion(self, messages: list[dict[str, Any]],
                         schema: dict[str, Any]) -> str:
        return self._structured_completion(messages, schema, "odm_agent_plan")

    def _structured_completion(self, messages: list[dict[str, Any]],
                               schema: dict[str, Any], schema_name: str) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            message = str(exc).lower()
            if "response_format" not in message and "json_schema" not in message:
                raise
            # 兼容不支持 response_format 的 OpenAI 兼容端点；仍由 Pydantic 做本地强校验。
            kwargs.pop("response_format")
            resp = self.client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def finalize(self, messages: list[dict[str, Any]]) -> LLMDecision:
        """以结构化最终回复收尾，避免无工具回合返回空文本。"""
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision_reason": {"type": "string", "minLength": 1, "maxLength": 240},
                "content": {"type": "string", "minLength": 1, "maxLength": 8000},
            },
            "required": ["decision_reason", "content"],
        }
        raw = self._structured_completion(messages, schema, "odm_agent_final")
        try:
            payload = json.loads(raw)
            return LLMDecision(
                type="final",
                thought=str(payload["decision_reason"]).strip(),
                content=str(payload["content"]).strip(),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # 兼容不支持 JSON Schema 的端点：有正文时仍可安全作为最终回复。
            if raw:
                return LLMDecision(
                    type="final",
                    thought="基于已完成的工具 Observation 生成最终回复。",
                    content=raw,
                )
            return LLMDecision(
                type="final",
                thought="模型未生成可用的收尾内容。",
                content="已完成资料检索，但本次未生成可用总结，请重新提交该问题。",
            )

    def replan(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               previous_plan: AgentPlan, reason: str) -> AgentPlan:
        replan_messages = [
            {"role": "system", "content": (
                "这是一次受控重规划。只调整尚未完成的后续步骤，保留已获得的工具 Observation，"
                "不要重复已完成步骤。重规划原因：" + reason
            )},
            {"role": "user", "content": "当前计划：" + json.dumps(previous_plan.to_dict(), ensure_ascii=False)},
            *messages,
        ]
        return self.plan(replan_messages, tools)

    def chat(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]]) -> LLMDecision:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "temperature": 0.2,
        }
        if tools:  # 无工具时(如纯文本归纳总纲)不传 tools,避免部分接口报错
            kwargs["tools"] = _to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        thought = (msg.content or "").strip()

        if msg.tool_calls:
            calls: list[ToolCall] = []
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                calls.append(ToolCall(id=call.id, name=call.function.name, input=args))
            names = "、".join(c.name for c in calls)
            return LLMDecision(
                type="tool_call",
                tool_calls=calls,
                thought=thought or f"调用工具 {names}。",
            )

        return LLMDecision(type="final", thought=thought, content=msg.content or "")


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Loop 内部消息映射为 OpenAI 兼容格式。

    Loop 内部已采用标准配对结构(assistant 带 tool_calls 发起、tool 带
    tool_call_id 回填),这里只做格式映射:工具入参/结果序列化为字符串,
    无需再伪造调用配对。
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        elif role == "assistant":
            tool_calls = m.get("tool_calls")
            if tool_calls:
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": [{
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("input") or {}, ensure_ascii=False),
                        },
                    } for c in tool_calls],
                })
            else:
                out.append({"role": "assistant", "content": m.get("content") or ""})
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id"),
                "content": json.dumps(m.get("result"), ensure_ascii=False),
            })
    return out
