"""策略验证服务:维护会话上下文、驱动 Agent Loop、记录指标。"""
from __future__ import annotations
from typing import Any

from ..agent.loop import AgentLoop, LoopResult
from ..llm.mock_provider import MockLLMProvider
from ..llm.base import LLMProvider
from ..db import cursor
from .. import config

# 简单的进程内会话存储(演示用;生产可换 Redis/DB)
_SESSIONS: dict[str, list[dict[str, Any]]] = {}


def _build_provider() -> LLMProvider:
    if config.LLM_PROVIDER == "ark":
        from ..llm.ark_provider import ArkLLMProvider
        return ArkLLMProvider()
    return MockLLMProvider()


_LOOP = AgentLoop(_build_provider())


def effective_system_prompt() -> str:
    """完整生效的系统提示词(技术流程 + 业务设定 + 策略总纲),供后台查看与策略验证使用。"""
    from . import settings_service
    parts = [_BASE_PROMPT]
    parts.append(
        "\n\n【业务设定(测试专家在后台可改,始终优先遵守,为最高铁律)】\n"
        + settings_service.get_directive()
    )
    summary = settings_service.get_summary().strip()
    if summary:
        parts.append(
            "\n\n【排查策略总纲(测试专家已确认,按此执行;"
            "recall_troubleshooting_strategy 取到的样本仅作具体参照,与本总纲冲突时以本总纲为准)】\n"
            + summary
        )
    return "".join(parts)


_BASE_PROMPT = (
    "你是一名 XZY ODM 问题排查策略 Agent。你的任务不是直接给结论,而是结合专家排查样本、"
    "测试 SOP、历史缺陷案例和当前问题上下文,给出可执行的排查路径。\n\n"
    "可用工具:\n"
    "- recall_troubleshooting_strategy:召回测试专家教过的问题排查策略样本。\n"
    "- test_sop_search:检索测试 SOP、刷机流程、日志采集规范和常见故障排查步骤。\n"
    "- defect_case_search:检索历史缺陷案例、类似问题处理记录、量产/试产问题案例。\n"
    "- escalate_to_expert:证据不足、风险较高或需要人工判断时,生成问题工单并升级测试专家。\n\n"
    "工作流程(务必遵守):\n"
    "1. 回答任何测试/研发问题前,【必须先调用 recall_troubleshooting_strategy】召回专家排查样本。\n"
    "2. 先判断问题类型,例如蓝牙、刷机、OTA、ANR、稳定性、兼容性、日志分析或量产问题。\n"
    "3. 不要直接套用某条历史案例给结论;先补齐复现条件、设备型号、固件版本、操作路径、日志和影响范围。\n"
    "4. 涉及流程、日志采集、刷机、OTA、ANR、稳定性等事实类排查动作时,必须调用 test_sop_search 核对。\n"
    "5. 涉及历史缺陷、类似问题、量产/试产问题或根因参考时,必须调用 defect_case_search 核对。\n"
    "6. 事实类信息必须来自测试 SOP 或历史缺陷工具,绝不可编造根因;查不到时说明证据不足。\n"
    "7. 遇到证据不足、风险较高、疑似底层协议/固件/硬件问题,或无法确认原因时,调用 escalate_to_expert。\n"
    "8. 输出结构尽量包含:问题判断、需要补充的信息、建议排查步骤、参考案例、是否建议升级。\n\n"
    "蓝牙标准策略样例:\n"
    "遇到蓝牙连接失败,不要直接判断是硬件问题。先确认问题是否稳定复现;确认设备型号、固件版本、"
    "耳机型号;检查是否只发生在特定蓝牙协议或特定设备组合;收集 bt_stack 日志;再对比历史缺陷案例;"
    "证据不足或疑似底层协议问题时,升级测试专家。\n\n"
    "用清晰、专业、可执行的中文回复,不要暴露内部工具名。"
)


def handle_chat(message: str, session_id: str = "demo") -> LoopResult:
    # system 每轮由 Loop 注入最新的 effective_system_prompt(总纲/业务设定实时生效),
    # 会话存储只保留用户消息和最终回复。
    history = _SESSIONS.get(session_id, [])

    result = _LOOP.run(message, history=history, system=effective_system_prompt())

    # 只把用户与最终回复(不含中间的 tool_calls / tool 消息)落进会话上下文
    _SESSIONS[session_id] = [
        m for m in result.session_messages
        if m.get("role") == "user"
        or (m.get("role") == "assistant" and not m.get("tool_calls"))
    ]

    _log_metrics(session_id, message, result)
    return result


def _log_metrics(session_id: str, question: str, result: LoopResult) -> None:
    hit = 1 if (result.kb_hit and not result.handoff) else 0
    with cursor() as cur:
        cur.execute(
            "INSERT INTO metrics_log (session_id, question, hit_kb, handoff) VALUES (?, ?, ?, ?)",
            (session_id, question, hit, 1 if result.handoff else 0),
        )


def reset_session(session_id: str = "demo") -> None:
    _SESSIONS.pop(session_id, None)


def refine_reply(question: str, reply: str, feedback: str, session_id: str = "demo") -> str:
    """专家纠错:测试专家对 AI 刚才这条回复提意见,AI 据此重答这一句(不写库,仅当场修正)。

    重答会遵守当前系统提示词(含总纲/铁律),并把会话里最后一条 assistant 替换为修正版,
    使后续对话从修正后的语境继续。
    """
    messages = [
        {"role": "system", "content": effective_system_prompt()},
        {"role": "user", "content": question},
        {"role": "assistant", "content": reply},
        {"role": "user", "content": (
            "以上是你刚才给出的排查建议。下面是【专家点评】(是专家给的纠偏意见,"
            "不是提问者的新问题,不要当成提问者的话来回应):\n" + feedback
            + "\n\n请你据此,把刚才这条排查建议重新说一遍。只输出修正后的内容,不要解释。")},
    ]
    decision = _LOOP.llm.chat(messages, [])
    revised = (decision.content or "").strip() or reply

    hist = _SESSIONS.get(session_id)
    if hist and hist[-1].get("role") == "assistant":
        hist[-1]["content"] = revised
    return revised


def commit_refinement(question: str, answer: str, feedback: str) -> dict:
    """把纠错结果固化为一条策略样本,并自动合并进总纲(状态合并)。"""
    from . import playbook_service
    note = "(实测纠偏)" + feedback if feedback else "(实测纠偏)"
    sample_id = playbook_service.add_sample(question, answer, note, source="refine")
    summary = regenerate_summary()
    return {"ok": True, "sample_id": sample_id, "summary": summary}


_EMPTY_SUMMARY = "策略样本库还是空的。去『专家教学模式』录入几条,我就能帮你归纳排查策略总纲了。"


def induce_playbook(existing: str = "") -> str:
    """从策略样本归纳『排查策略总纲』。

    existing 非空时执行【状态合并】:完整保留专家已改写的总纲规则,
    只把样本里体现出、但现有总纲还没覆盖的新策略补进去;冲突以专家现有总纲为准。
    """
    from . import playbook_service
    samples = playbook_service.recall_all()
    if not samples:
        return _EMPTY_SUMMARY
    body = "\n\n".join(
        f"{i + 1}. 问题现象:{s['question']}\n   建议排查路径:{s['answer']}\n   策略原因:{s.get('note', '') or '(未填)'}"
        for i, s in enumerate(samples)
    )
    if existing.strip():
        messages = [
            {"role": "system", "content": (
                "你是资深 ODM 测试策略专家。专家已经有一份【现有排查策略总纲】,其中可能有手动修订或补充的规则,"
                "这些必须尊重并原样保留。现在给你一批策略样本。请在【完整保留现有总纲里的规则和措辞倾向】"
                "的前提下,把样本里体现出、但现有总纲还没覆盖的新排查策略补充进去;若样本与现有总纲有冲突,"
                "一律以现有总纲为准。输出更新后的完整总纲,条目化、简洁;不要删除已有规则,不要复述原始样本。")},
            {"role": "user", "content": (
                "【现有排查策略总纲(专家已改写,须保留)】:\n" + existing.strip()
                + "\n\n【全部策略样本】:\n" + body)},
        ]
    else:
        messages = [
            {"role": "system", "content": (
                "你是资深 ODM 测试策略专家。下面是测试专家教给『XZY ODM 问题排查策略 Agent』的一批示范"
                "(问题现象 + 建议排查路径 + 策略原因)。请你从这些具体示范中,归纳提炼出"
                "『排查策略总纲』:用简洁条目列出通用排查原则,例如先确认复现、补齐环境与日志、"
                "再查 SOP 和历史缺陷、证据不足时升级专家。只输出总纲本身,不要复述原始样本。")},
            {"role": "user", "content": "以下是全部策略样本:\n\n" + body},
        ]
    decision = _LOOP.llm.chat(messages, [])
    return (decision.content or "").strip() or "(暂未归纳出内容)"


def get_or_build_summary() -> str:
    """返回已保存的总纲;若从未生成过,则首次自动归纳并存下来(保留后续编辑)。"""
    from . import settings_service
    saved = settings_service.get_summary()
    if saved.strip():
        return saved
    fresh = induce_playbook()
    if fresh and fresh != _EMPTY_SUMMARY:
        settings_service.set_summary(fresh)
    return fresh


def regenerate_summary() -> str:
    """状态合并:以专家改写后的现有总纲为基底,融合全部样本,重新归纳并保存。"""
    from . import settings_service
    merged = induce_playbook(existing=settings_service.get_summary())
    if merged and merged != _EMPTY_SUMMARY:
        settings_service.set_summary(merged)
    return merged
