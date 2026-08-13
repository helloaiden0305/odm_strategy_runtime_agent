"""Agent Loop 引擎。

核心循环:推理 → 选工具 → 调用 → 观察结果 → 再决策 → 终止。
内置边界控制(最大步数 / 超时),并全程记录轨迹用于前端可视化。

引擎只依赖 LLMProvider 抽象与 Tool 接口,与具体模型/工具实现解耦。
"""
from __future__ import annotations
import copy
import time
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLMProvider
from .. import config
from .tools import build_registry


@dataclass
class LoopResult:
    reply: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    handoff: bool = False
    ticket_id: int | None = None
    kb_hit: bool = False
    session_messages: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.tools = build_registry()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    @staticmethod
    def _result_count(result: dict[str, Any]) -> int:
        """从统一结果字段中提取便于 trace 检索的数量。"""
        if not isinstance(result, dict):
            return 0
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for key in ("count", "total"):
            value = data.get(key, result.get(key))
            if isinstance(value, int):
                return value
        for key in ("samples", "defects", "sops", "cases"):
            value = data.get(key, result.get(key))
            if isinstance(value, list):
                return len(value)
        if data.get("sop") or result.get("sop") or result.get("ticket_id"):
            return 1
        return 0

    def run(self, user_message: str,
            history: list[dict[str, Any]] | None = None,
            system: str | None = None) -> LoopResult:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages += list(history or [])
        messages.append({"role": "user", "content": user_message})

        trace: list[dict[str, Any]] = []
        handoff = False
        ticket_id: int | None = None
        kb_hit = False

        started = time.time()
        step = 0
        reply = "抱歉,系统繁忙,请稍后再试。"

        while step < config.MAX_LOOP_STEPS:
            if time.time() - started > config.LOOP_TIMEOUT_SECONDS:
                reply = "处理超时,已为您转接人工客服。"
                trace.append({"step": step, "type": "final",
                              "content": "(超时,边界控制触发)"})
                break

            step += 1
            schemas = self._tool_schemas()

            # ① LLM 调用前:记录本步喂给模型的完整上下文(调试"上下文是什么")
            trace.append({"step": step, "type": "llm_call",
                          "messages": copy.deepcopy(messages),
                          "available_tools": [t["name"] for t in schemas]})

            decision = self.llm.chat(messages, schemas)

            # ② LLM 返回后:记录模型原始决策(调试"模型决定做什么")
            trace.append({"step": step, "type": "llm_response",
                          "decision": decision.type,
                          "thought": decision.thought,
                          "tool_calls": [{"id": c.id, "name": c.name, "input": c.input}
                                         for c in decision.tool_calls],
                          "content": decision.content})

            # 记录"思考"
            if decision.thought:
                trace.append({"step": step, "type": "think",
                              "content": decision.thought})

            if decision.type == "final":
                reply = decision.content or ""
                trace.append({"step": step, "type": "final", "content": reply})
                messages.append({"role": "assistant", "content": reply})
                break

            # tool_calls:模型本步可请求一个或多个工具(并行);按标准
            # assistant(发起 tool_calls)→ tool(带 tool_call_id 回填)结构记录
            calls = decision.tool_calls
            messages.append({
                "role": "assistant",
                "content": decision.thought or "",
                "tool_calls": [{"id": c.id, "name": c.name, "input": c.input}
                               for c in calls],
            })
            for call in calls:
                tool = self.tools.get(call.name)
                tool_exists = tool is not None
                input_valid = False
                validation_error: dict[str, Any] | None = None
                payload: dict[str, Any] = {}
                trace.append({"step": step, "type": "tool_call",
                              "tool": call.name, "input": call.input,
                              "tool_exists": tool_exists,
                              "input_valid": None,
                              "result_ok": None,
                              "result_count": None})
                try:
                    if not tool_exists:
                        result = {
                            "ok": False,
                            "tool": call.name,
                            "data": {},
                            "error": f"未知工具:{call.name}",
                            "meta": {},
                        }
                    else:
                        input_valid, payload, validation_error = tool.validate_input(call.input or {})
                        if not input_valid:
                            result = tool.fail(
                                validation_error["message"],
                                meta={"validation_error": validation_error},
                            )
                            trace.append({"step": step, "type": "tool_validation_error",
                                          "tool": call.name,
                                          "input": call.input,
                                          "tool_exists": tool_exists,
                                          "input_valid": input_valid,
                                          "error": validation_error})
                        else:
                            result = tool.run(**payload)
                except Exception as exc:  # 工具异常转成观测结果回填,让模型自行纠错
                    result = {
                        "ok": False,
                        "tool": call.name,
                        "data": {},
                        "error": f"工具 {call.name} 执行出错:{exc}",
                        "meta": {"exception_type": type(exc).__name__},
                    }

                is_error = (
                    isinstance(result, dict)
                    and (result.get("ok") is False or bool(result.get("error")))
                )
                result_ok = not is_error
                result_count = self._result_count(result) if isinstance(result, dict) else 0
                trace.append({"step": step, "type": "tool_result",
                              "tool": call.name, "output": result,
                              "is_error": is_error,
                              "tool_exists": tool_exists,
                              "input_valid": input_valid,
                              "result_ok": result_ok,
                              "result_count": result_count})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "name": call.name, "result": result})

                # 旁路记录关键信号(用于指标与返回)
                if call.name == "escalate_to_expert" and isinstance(result, dict):
                    handoff = True
                    ticket_id = result.get("ticket_id")
                if call.name == "recall_troubleshooting_strategy" and isinstance(result, dict):
                    kb_hit = kb_hit or bool(result.get("count"))
        else:
            # while 正常结束(达到最大步数仍未 final)
            reply = "这个问题比较复杂,已为您转接人工客服跟进。"
            trace.append({"step": step, "type": "final",
                          "content": "(达到最大循环步数,边界控制触发)"})

        return LoopResult(reply=reply, trace=trace, handoff=handoff,
                          ticket_id=ticket_id, kb_hit=kb_hit,
                          session_messages=messages)
