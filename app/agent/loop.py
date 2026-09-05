"""Agent Loop 引擎。

核心循环:推理 → 选工具 → 调用 → 观察结果 → 再决策 → 终止。
内置边界控制(最大步数 / 超时),并全程记录轨迹用于前端可视化。

引擎只依赖 LLMProvider 抽象与 Tool 接口,与具体模型/工具实现解耦。
"""
from __future__ import annotations
import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm.base import AgentPlan, LLMProvider, PlanStep
from .. import config
from .plan import build_safe_default_plan, validate_plan
from .tools import build_registry


@dataclass
class LoopResult:
    reply: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    handoff: bool = False
    ticket_id: int | None = None
    kb_hit: bool = False
    session_messages: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False


class AgentLoop:
    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.tools = build_registry()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    @staticmethod
    def _next_plan_step(plan: AgentPlan) -> PlanStep | None:
        for item in plan.steps:
            if item.status in {"pending", "running"}:
                return item
        return None

    @staticmethod
    def _with_plan_context(messages: list[dict[str, Any]],
                           plan: AgentPlan, step: PlanStep | None) -> list[dict[str, Any]]:
        """向本轮模型调用补充短计划上下文，不把运行态提示写入会话历史。"""
        if step is None:
            return copy.deepcopy(messages)
        context = (
            "【当前受控执行阶段】\n"
            f"计划目标：{plan.goal}\n"
            f"阶段：{step.goal}\n"
            f"待补证据：{'、'.join(plan.evidence_gap) or '无'}\n"
            f"允许工具：{'、'.join(step.allowed_tools)}\n"
            f"退出条件：{step.exit_condition}\n"
            "只能在允许工具中选择；证据不足时说明待补充信息或按计划进入升级路径。"
        )
        enriched = copy.deepcopy(messages)
        if enriched and enriched[0].get("role") == "system":
            enriched[0]["content"] = (enriched[0].get("content") or "") + "\n\n" + context
        else:
            enriched.insert(0, {"role": "system", "content": context})
        return enriched

    @staticmethod
    def _set_plan_state(trace: list[dict[str, Any]], step_number: int,
                        item: PlanStep, status: str, reason: str) -> None:
        if item.status == status:
            return
        item.status = status
        trace.append({
            "step": step_number,
            "type": "plan_state",
            "plan_step": item.id,
            "status": status,
            "content": reason,
        })

    @staticmethod
    def _planner_failure_reason(exc: Exception) -> str:
        """向 Trace 提供可定位但不暴露 Provider 原始响应的降级原因。"""
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "json" in name or "json" in message:
            return "规划结果不是合规 JSON。"
        if "timeout" in name or "timeout" in message:
            return "规划请求超时。"
        if "auth" in name or "api_key" in message or "401" in message:
            return "规划模型鉴权失败。"
        if "connection" in name or "connect" in message:
            return "规划模型连接失败。"
        if "valueerror" in name:
            return "规划结构不符合约定。"
        return f"规划请求异常（{type(exc).__name__}）。"

    def _create_plan(self, messages: list[dict[str, Any]],
                     trace: list[dict[str, Any]]) -> AgentPlan:
        schemas = self._tool_schemas()
        try:
            plan = self.llm.plan(messages, schemas)
            trace.append({"step": 0, "type": "plan_created", "plan": plan.to_dict(),
                          "content": plan.decision_reason})
        except Exception as exc:
            plan = build_safe_default_plan()
            trace.append({"step": 0, "type": "plan_invalid",
                          "content": "Planner 输出不可用，已采用安全默认计划。",
                          "error": self._planner_failure_reason(exc)})

        guard = validate_plan(plan, set(self.tools))
        trace.append({"step": 0, "type": "plan_guard", "ok": guard.valid,
                      "reasons": guard.reasons,
                      "content": "计划校验通过。" if guard.valid else "计划校验未通过。"})
        if guard.valid:
            return plan

        fallback = build_safe_default_plan()
        fallback_guard = validate_plan(fallback, set(self.tools))
        trace.append({"step": 0, "type": "plan_created", "plan": fallback.to_dict(),
                      "fallback": True, "content": fallback.decision_reason})
        trace.append({"step": 0, "type": "plan_guard", "ok": fallback_guard.valid,
                      "fallback": True, "reasons": fallback_guard.reasons,
                      "content": "安全默认计划校验通过。" if fallback_guard.valid
                      else "安全默认计划校验失败。"})
        return fallback

    @staticmethod
    def _merge_replanned_plan(previous: AgentPlan, candidate: AgentPlan) -> AgentPlan:
        """重规划只改变尚未完成部分，已结束步骤与 Observation 语义保持稳定。"""
        previous_states = {
            item.id: item.status for item in previous.steps
            if item.status in {"completed", "blocked", "skipped"}
        }
        for item in candidate.steps:
            if item.id in previous_states:
                item.status = previous_states[item.id]
        candidate.replan_count = previous.replan_count + 1
        return candidate

    def _replan(self, messages: list[dict[str, Any]], plan: AgentPlan,
                reason: str, trace: list[dict[str, Any]], step_number: int) -> AgentPlan:
        schemas = self._tool_schemas()
        try:
            candidate = self.llm.replan(messages, schemas, plan, reason)
            candidate = self._merge_replanned_plan(plan, candidate)
            guard = validate_plan(candidate, set(self.tools))
            trace.append({"step": step_number, "type": "plan_guard", "ok": guard.valid,
                          "replan": True, "reasons": guard.reasons,
                          "content": "重规划校验通过。" if guard.valid else "重规划校验未通过。"})
            if not guard.valid:
                raise ValueError("; ".join(guard.reasons))
            trace.append({"step": step_number, "type": "replan", "reason": reason,
                          "plan": candidate.to_dict(), "replan_count": candidate.replan_count,
                          "content": "已按新的 Observation 修订剩余步骤。"})
            return candidate
        except Exception as exc:
            fallback = self._merge_replanned_plan(plan, build_safe_default_plan())
            guard = validate_plan(fallback, set(self.tools))
            trace.append({"step": step_number, "type": "plan_guard", "ok": guard.valid,
                          "replan": True, "fallback": True, "reasons": guard.reasons,
                          "content": "重规划不可用，改用安全默认剩余步骤。"})
            trace.append({"step": step_number, "type": "replan", "reason": reason,
                          "plan": fallback.to_dict(), "replan_count": fallback.replan_count,
                          "fallback": True,
                          "content": f"重规划不可用，已采用安全默认步骤：{type(exc).__name__}。"})
            return fallback

    @staticmethod
    def _final_guard_reasons(plan: AgentPlan, current_step: PlanStep | None) -> list[str]:
        reasons: list[str] = []
        recall = plan.steps[0] if plan.steps else None
        if not recall or recall.status != "completed":
            reasons.append("策略召回步骤尚未完成")
        blocked = [
            item.id for item in plan.steps
            if item.status == "blocked" and item.required_evidence
        ]
        if blocked:
            reasons.append("必要证据步骤受阻: " + ", ".join(blocked))
        if current_step and current_step.id != "conclude_or_escalate":
            reasons.append(f"当前计划步骤 {current_step.id} 尚未完成")
        return reasons

    def _force_expert_handoff(self, user_message: str,
                              handoff_context: dict[str, Any] | None,
                              should_cancel: Callable[[], bool] | None,
                              trace: list[dict[str, Any]], step_number: int,
                              plan_step: PlanStep | None,
                              reasons: list[str]) -> tuple[bool, int | None, str]:
        """Final Guard 无法放行时，由代码走已有的专家升级兜底。"""
        tool = self.tools.get("escalate_to_expert")
        payload = self._enhance_handoff_input({
            "question": user_message,
            "context": "Final Guard 未满足：" + "；".join(reasons),
        }, handoff_context)
        trace.append({"step": step_number, "type": "tool_call",
                      "tool": "escalate_to_expert", "input": payload,
                      "tool_exists": tool is not None, "plan_allowed": True,
                      "input_valid": None, "result_ok": None, "result_count": None,
                      "forced_by_final_guard": True})
        if should_cancel and should_cancel():
            return False, None, ""
        if tool is None:
            result = {"ok": False, "error": "升级工具不可用", "data": {}}
            valid = False
        else:
            valid, validated, validation_error = tool.validate_input(payload)
            if not valid:
                result = tool.fail(validation_error["message"], meta={"validation_error": validation_error})
            else:
                result = tool.run(**validated, _should_cancel=should_cancel)
        is_error = not isinstance(result, dict) or result.get("ok") is False or bool(result.get("error"))
        ticket_id = result.get("ticket_id") if isinstance(result, dict) else None
        trace.append({"step": step_number, "type": "tool_result",
                      "tool": "escalate_to_expert", "output": result,
                      "is_error": is_error, "tool_exists": tool is not None,
                      "plan_allowed": True, "input_valid": valid,
                      "result_ok": not is_error,
                      "result_count": self._result_count(result) if isinstance(result, dict) else 0,
                      "forced_by_final_guard": True})
        if plan_step:
            self._set_plan_state(
                trace, step_number, plan_step,
                "completed" if not is_error else "blocked",
                "Final Guard 触发专家升级兜底。" if not is_error else "专家升级兜底未成功。",
            )
        if is_error:
            return False, ticket_id, "当前证据不足，且专家工单暂未创建成功，请补充关键日志后重试。"
        return True, ticket_id, "当前证据不足，已生成问题工单并升级测试专家继续跟进。"

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

    @staticmethod
    def _enhance_handoff_input(payload: dict[str, Any],
                               context: dict[str, Any] | None) -> dict[str, Any]:
        """补齐升级工单复盘上下文,不改变工具本身的对外契约。"""
        if not context:
            return payload
        enhanced = dict(payload)
        phenomenon = (context.get("phenomenon") or "").strip()
        reason = (context.get("reason") or enhanced.get("context") or "需要测试专家确认").strip()
        attempted = (context.get("attempted") or "").strip()
        missing = (context.get("missing") or "").strip()
        session_summary = (context.get("session_summary") or "").strip()

        if phenomenon:
            enhanced["question"] = phenomenon
        elif context.get("missing_phenomenon"):
            reason = "缺少原始问题现象,需要专家先补充上下文。"

        blocks = [
            ("问题现象", enhanced.get("question", "")),
            ("升级原因", reason),
            ("已尝试", attempted),
            ("缺失证据", missing),
            ("当前会话摘要", session_summary),
        ]
        enhanced["context"] = "\n".join(
            f"{title}:{value}" for title, value in blocks if value
        )
        return enhanced

    def run(self, user_message: str,
            history: list[dict[str, Any]] | None = None,
            system: str | None = None,
            handoff_context: dict[str, Any] | None = None,
            should_cancel: Callable[[], bool] | None = None) -> LoopResult:
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

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        def cancelled_result() -> LoopResult:
            trace.append({"step": step, "type": "cancelled", "content": "(本轮策略验证已中止)"})
            return LoopResult(reply="", trace=trace, handoff=handoff,
                              ticket_id=ticket_id, kb_hit=kb_hit,
                              session_messages=messages, cancelled=True)

        if cancelled():
            return cancelled_result()
        plan = self._create_plan(messages, trace)
        if cancelled():
            return cancelled_result()

        while step < config.MAX_LOOP_STEPS:
            if cancelled():
                return cancelled_result()
            if time.time() - started > config.LOOP_TIMEOUT_SECONDS:
                current_plan_step = self._next_plan_step(plan)
                handoff, ticket_id, reply = self._force_expert_handoff(
                    user_message, handoff_context, should_cancel, trace, step,
                    current_plan_step, ["运行超时，边界控制触发"],
                )
                if cancelled():
                    return cancelled_result()
                trace.append({"step": step, "type": "guard_forced_finish", "content": reply,
                              "reasons": ["运行超时，边界控制触发"]})
                messages.append({"role": "assistant", "content": reply})
                break

            blocked_steps = [item for item in plan.steps if item.status == "blocked"]
            if blocked_steps and plan.replan_count < config.MAX_PLAN_REPLANS:
                reason = "计划步骤受阻: " + ", ".join(item.id for item in blocked_steps)
                plan = self._replan(messages, plan, reason, trace, step)
                if cancelled():
                    return cancelled_result()

            step += 1
            current_plan_step = self._next_plan_step(plan)
            all_schemas = self._tool_schemas()
            schemas = [
                schema for schema in all_schemas
                if current_plan_step is not None
                and schema["name"] in current_plan_step.allowed_tools
            ]
            if current_plan_step is not None and current_plan_step.status == "pending":
                self._set_plan_state(trace, step, current_plan_step, "running", "开始执行当前计划步骤。")
            model_messages = self._with_plan_context(messages, plan, current_plan_step)

            # ① LLM 调用前:记录本步喂给模型的完整上下文(调试"上下文是什么")
            trace.append({"step": step, "type": "llm_call",
                          "messages": copy.deepcopy(model_messages),
                          "available_tools": [t["name"] for t in schemas],
                          "plan_step": current_plan_step.id if current_plan_step else None})

            decision = self.llm.chat(model_messages, schemas)

            if cancelled():
                return cancelled_result()

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
                if cancelled():
                    return cancelled_result()
                guard_reasons = self._final_guard_reasons(plan, current_plan_step)
                trace.append({"step": step, "type": "final_guard", "ok": not guard_reasons,
                              "reasons": guard_reasons,
                              "content": "最终回复满足计划与证据门禁。" if not guard_reasons
                              else "最终回复尚未满足计划与证据门禁。"})
                if guard_reasons and plan.replan_count < config.MAX_PLAN_REPLANS:
                    plan = self._replan(
                        messages, plan, "Final Guard 拒绝直接收尾: " + "；".join(guard_reasons),
                        trace, step,
                    )
                    if cancelled():
                        return cancelled_result()
                    continue
                if guard_reasons:
                    handoff, ticket_id, reply = self._force_expert_handoff(
                        user_message, handoff_context, should_cancel, trace, step,
                        current_plan_step, guard_reasons,
                    )
                    if cancelled():
                        return cancelled_result()
                    trace.append({"step": step, "type": "guard_forced_finish", "content": reply,
                                  "reasons": guard_reasons})
                    messages.append({"role": "assistant", "content": reply})
                    break
                reply = decision.content or ""
                if current_plan_step:
                    self._set_plan_state(
                        trace, step, current_plan_step, "completed",
                        "Final Guard 已允许以当前证据收尾。",
                    )
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
                if cancelled():
                    return cancelled_result()
                tool = self.tools.get(call.name)
                tool_exists = tool is not None
                plan_allowed = bool(
                    current_plan_step
                    and call.name in current_plan_step.allowed_tools
                )
                input_valid = False
                validation_error: dict[str, Any] | None = None
                payload: dict[str, Any] = {}
                tool_input = self._enhance_handoff_input(call.input or {}, handoff_context) \
                    if call.name == "escalate_to_expert" else (call.input or {})
                trace.append({"step": step, "type": "tool_call",
                              "tool": call.name, "input": tool_input,
                              "tool_exists": tool_exists,
                              "plan_allowed": plan_allowed,
                              "input_valid": None,
                              "result_ok": None,
                              "result_count": None})
                try:
                    if not plan_allowed:
                        result = {
                            "ok": False,
                            "tool": call.name,
                            "data": {},
                            "error": f"当前计划步骤不允许调用工具:{call.name}",
                            "meta": {"plan_step": current_plan_step.id if current_plan_step else None},
                        }
                    elif not tool_exists:
                        result = {
                            "ok": False,
                            "tool": call.name,
                            "data": {},
                            "error": f"未知工具:{call.name}",
                            "meta": {},
                        }
                    else:
                        input_valid, payload, validation_error = tool.validate_input(tool_input)
                        if not input_valid:
                            result = tool.fail(
                                validation_error["message"],
                                meta={"validation_error": validation_error},
                            )
                            trace.append({"step": step, "type": "tool_validation_error",
                                          "tool": call.name,
                                          "input": tool_input,
                                          "tool_exists": tool_exists,
                                          "input_valid": input_valid,
                                          "error": validation_error})
                        else:
                            run_kwargs = dict(payload)
                            if call.name == "escalate_to_expert":
                                run_kwargs["_should_cancel"] = should_cancel
                            result = tool.run(**run_kwargs)
                except Exception as exc:  # 工具异常转成观测结果回填,让模型自行纠错
                    result = {
                        "ok": False,
                        "tool": call.name,
                        "data": {},
                        "error": f"工具 {call.name} 执行出错:{exc}",
                        "meta": {"exception_type": type(exc).__name__},
                    }

                # 升级工具已经是有副作用的写入。若刚写完即收到取消,
                # 将 ticket_id 交给服务层只回滚本次运行生成的那张工单。
                if call.name == "escalate_to_expert" and isinstance(result, dict):
                    handoff = bool(result.get("ticket_id"))
                    ticket_id = result.get("ticket_id")

                if cancelled():
                    return cancelled_result()

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
                              "plan_allowed": plan_allowed,
                              "input_valid": input_valid,
                              "result_ok": result_ok,
                              "result_count": result_count})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "name": call.name, "result": result})

                # 旁路记录关键信号(用于指标与返回)
                if call.name == "recall_troubleshooting_strategy" and isinstance(result, dict):
                    kb_hit = kb_hit or bool(result.get("count"))

                if current_plan_step and plan_allowed:
                    if result_ok:
                        self._set_plan_state(
                            trace, step, current_plan_step, "completed",
                            "已获得当前步骤的工具 Observation。",
                        )
                    else:
                        self._set_plan_state(
                            trace, step, current_plan_step, "blocked",
                            "当前步骤的工具调用未能产生可用 Observation。",
                        )
        else:
            # while 正常结束(达到最大步数仍未 final)
            current_plan_step = self._next_plan_step(plan)
            handoff, ticket_id, reply = self._force_expert_handoff(
                user_message, handoff_context, should_cancel, trace, step,
                current_plan_step, ["达到最大循环步数，边界控制触发"],
            )
            if cancelled():
                return cancelled_result()
            trace.append({"step": step, "type": "guard_forced_finish", "content": reply,
                          "reasons": ["达到最大循环步数，边界控制触发"]})
            messages.append({"role": "assistant", "content": reply})

        if cancelled():
            return cancelled_result()

        return LoopResult(reply=reply, trace=trace, handoff=handoff,
                          ticket_id=ticket_id, kb_hit=kb_hit,
                          session_messages=messages)
