"""规则桩 LLM(离线演示用)。

这是为了**离线、可重复**地演示 ODM 问题排查策略场景下的 Agent Loop 而写的"假大脑":
它读对话历史(含已执行的工具结果),用规则模拟真实模型的"决定调哪个工具 / 何时收尾"。

>>> 换成真实模型(ArkProvider)时,语义理解取代这里的规则即可,Loop 与工具都不用动。
"""
from __future__ import annotations
import re
import time
from typing import Any

from .base import AgentPlan, LLMProvider, LLMDecision, PlanStep, ToolCall
from .. import config

# 各类意图的关键词
_HANDOFF_RE = re.compile(r"(升级测试专家|升级专家|判断不了|看不准|人工判断|转专家|专家)")
_SOP_RE = re.compile(r"(怎么排查|下一步|刷机|OTA|ota|蓝牙|日志|logcat|bt_stack|traces|稳定性|压测|卡死|ANR|anr|复现|固件)")
_CASE_RE = re.compile(r"(案例|历史缺陷|类似问题|量产|试产|根因|处理记录|bug|BUG|缺陷)")
_HISTORY_LOOKUP_RE = re.compile(r"(有没有|是否有|有无|查(?:询|一下)?|检索).{0,20}(案例|历史缺陷|类似问题|处理记录)|类似.{0,20}(案例|历史缺陷)")
_CONFIRMED_SUMMARY_RE = re.compile(
    r"【专家确认保护基底\(必须原样保留\)】[:：]?\s*(.*?)\n\n【全部策略样本】",
    re.S,
)


def _call(name: str, tool_input: dict[str, Any], thought: str) -> LLMDecision:
    """构造单工具调用决策(桩场景一步只调一个工具)。"""
    return LLMDecision(
        type="tool_call",
        tool_calls=[ToolCall(id=f"call_{name}", name=name, input=tool_input)],
        thought=thought,
    )


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "") or ""
    return ""


def _is_history_lookup(question: str) -> bool:
    """仅查询历史资料，不等同于要求给出具体故障排查动作。"""
    return bool(_CASE_RE.search(question) and _HISTORY_LOOKUP_RE.search(question))


def _tool_results_since_last_user(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """返回 {tool_name: result} —— 本轮用户提问后已执行过的工具及其结果。"""
    results: dict[str, Any] = {}
    collecting = False
    for m in messages:
        if m.get("role") == "user":
            results = {}          # 遇到新的用户提问,重置
            collecting = True
            continue
        if collecting and m.get("role") == "tool":
            results[m.get("name")] = m.get("result")
    return results


def _offline_text_response(messages: list[dict[str, Any]]) -> LLMDecision:
    """无工具场景的最小离线回复，覆盖总纲归纳与专家纠错演示。"""
    latest = _last_user_message(messages)
    if "策略样本" in latest:
        protected = _CONFIRMED_SUMMARY_RE.search(latest)
        if protected:
            return LLMDecision(
                type="final",
                thought="离线模式下保留专家确认总纲，不伪造新增策略归纳。",
                content=protected.group(1).strip(),
            )
        samples = re.findall(
            r"问题现象:(.*?)\n\s*建议排查路径:(.*?)\n\s*策略原因:(.*?)(?=\n\n\d+\. 问题现象:|\Z)",
            latest,
            re.S,
        )
        if samples:
            rules = []
            for index, (_, answer, reason) in enumerate(samples, start=1):
                path = " ".join(answer.split())
                note = " ".join(reason.split())
                rules.append(f"{index}. {path}" + (f" 策略原因:{note}" if note else ""))
            return LLMDecision(
                type="final",
                thought="根据专家样本归纳可复用的排查原则。",
                content="\n".join(rules),
            )
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return LLMDecision(
                type="final",
                thought="离线模式下保留已确认的排查建议。",
                content=message["content"],
            )
    return LLMDecision(
        type="final",
        thought="离线模式下缺少可归纳的样本。",
        content="请先补充问题现象、复现条件、版本和关键日志，再进入下一步排查。",
    )


class MockLLMProvider(LLMProvider):
    def plan(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]]) -> AgentPlan:
        question = _last_user_message(messages)
        steps = [
            PlanStep(
                id="recall_strategy",
                goal="确认已有专家策略是否可复用。",
                allowed_tools=["recall_troubleshooting_strategy"],
                required_evidence=["策略样本召回结果"],
                exit_condition="已得到策略样本结果或确认未命中。",
                fallback="进入补充证据或专家升级判断。",
            ),
        ]
        history_lookup = _is_history_lookup(question)
        evidence_gap = [] if history_lookup else ["复现条件", "设备型号", "固件版本"]
        if _HANDOFF_RE.search(question):
            steps.append(PlanStep(
                id="conclude_or_escalate",
                goal="基于用户明确升级请求创建问题工单。",
                allowed_tools=["escalate_to_expert"],
                exit_condition="已创建专家工单或明确说明创建失败原因。",
                fallback="明确说明待补充信息。",
            ))
        elif _CASE_RE.search(question):
            steps.append(PlanStep(
                id="collect_case",
                goal="核对历史缺陷或类似问题处理记录。",
                allowed_tools=["defect_case_search"],
                required_evidence=["历史缺陷结果"],
                exit_condition="已命中案例或明确未命中。",
                fallback="保留历史对比待补充项。",
            ))
        elif _SOP_RE.search(question):
            steps.append(PlanStep(
                id="collect_sop",
                goal="核对测试 SOP、日志规范或标准排查步骤。",
                allowed_tools=["test_sop_search"],
                required_evidence=["测试 SOP 结果"],
                exit_condition="已命中 SOP 或明确未命中。",
                fallback="保留日志与环境待补充项。",
            ))
            evidence_gap.append("关键日志")
        if not _HANDOFF_RE.search(question):
            steps.append(PlanStep(
                id="conclude_or_escalate",
                goal="基于已有证据输出建议、追问或升级测试专家。",
                allowed_tools=["escalate_to_expert"],
                exit_condition="完成受控结论、待补充说明或专家升级。",
                fallback="明确说明当前无法确认的原因。",
            ))
        return AgentPlan(
            goal="验证当前问题能否依据已有策略形成可执行的下一步。",
            decision_reason=(
                "用户仅查询历史参考，召回策略并核对缺陷案例后即可收尾。"
                if history_lookup
                else "当前缺少完整环境与证据，需要先召回策略并按问题类型补齐依据。"
            ),
            evidence_gap=evidence_gap,
            steps=steps,
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMDecision:
        if config.MOCK_LLM_DELAY_SECONDS:
            time.sleep(config.MOCK_LLM_DELAY_SECONDS)
        if not tools:
            return _offline_text_response(messages)
        question = _last_user_message(messages)
        done = _tool_results_since_last_user(messages)

        # 1) 回答任何 ODM 问题前,先召回专家排查策略样本
        if "recall_troubleshooting_strategy" not in done:
            return _call("recall_troubleshooting_strategy", {"query": question},
                         "先召回测试专家沉淀的问题排查策略样本。")

        pb = done.get("recall_troubleshooting_strategy") or {}

        # 2) 用户明确要求升级测试专家
        if _HANDOFF_RE.search(question):
            if "escalate_to_expert" not in done:
                return _call("escalate_to_expert", {"question": question, "context": "用户主动要求升级测试专家"},
                             "用户明确要求升级测试专家,生成问题工单。")
            return LLMDecision(type="final", thought="已升级测试专家。",
                               content="已生成问题工单并升级测试专家,请补充复现环境、版本号和关键日志,专家会继续跟进。")

        # 3) 需要参考历史缺陷 —— 查缺陷案例
        if _CASE_RE.search(question) and "defect_case_search" not in done:
            return _call("defect_case_search", {"query": question},
                         "需要参考历史缺陷案例,检索缺陷案例库。")

        # 只查询历史案例时，案例结果就是目标证据，不要求补齐某台设备的排查日志。
        if _is_history_lookup(question) and "defect_case_search" in done:
            return LLMDecision(type="final", thought="历史案例查询已获得可用参考。",
                               content=_answer(pb, done))

        # 4) 涉及流程、日志、复现和定位动作 —— 查测试 SOP
        if _SOP_RE.search(question) and "test_sop_search" not in done:
            return _call("test_sop_search", {"query": question},
                         "问题涉及排查流程或日志规范,检索测试 SOP 核对。")

        # 5) 策略样本为空且也没有可用 SOP/缺陷观察 —— 升级测试专家
        if not pb.get("count") and "test_sop_search" not in done and "defect_case_search" not in done:
            if "escalate_to_expert" not in done:
                return _call("escalate_to_expert",
                             {"question": question, "context": "策略样本库为空或未命中,需要专家判断。"},
                             "策略样本不足,需要升级测试专家。")
            return LLMDecision(type="final", thought="已升级测试专家。",
                               content="这个问题当前策略样本不足,已生成问题工单并升级测试专家处理。")

        # 6) 收尾:按召回到的策略样本 + 已查到的信息作答
        return LLMDecision(type="final", thought="按召回到的排查策略组织回复。",
                           content=_answer(pb, done))


def _answer(pb: dict[str, Any], done: dict[str, Any]) -> str:
    """把策略样本 + SOP + 缺陷案例拼成回复(真实模型会语义精判并自然生成)。"""
    samples = pb.get("samples") or []
    sop = (done.get("test_sop_search") or {}).get("sop")
    defects = (done.get("defect_case_search") or {}).get("defects")

    parts: list[str] = []
    if samples:
        parts.append(samples[0].get("answer", ""))
    if sop:
        parts.append(f"\n参考 SOP:{sop['name']}。重点看:{sop.get('outline', '')}")
    if defects:
        c = defects[0]
        parts.append(f"\n参考缺陷:{c['id']}({c['module']})。现象:{c['symptom']}；历史根因:{c['root_cause']}。")
    return ("".join(p for p in parts if p)).strip() or "请先补充复现环境、版本号、日志类型和是否稳定复现,再进入下一步排查。"
