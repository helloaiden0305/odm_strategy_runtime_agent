"""Agent Loop 收尾与信息查询路径的回归测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TEMP_DIR.name) / "agent_loop.db")
os.environ["RESET_DEMO_RUNTIME_ON_START"] = "false"

from app.agent.loop import AgentLoop
from app.llm.base import AgentPlan, LLMDecision, LLMProvider, PlanStep, ToolCall
from app.llm.mock_provider import MockLLMProvider


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def schema(self) -> dict:
        return {"name": self.name}

    def validate_input(self, payload: dict) -> tuple[bool, dict, None]:
        return True, dict(payload), None

    def run(self, **kwargs: object) -> dict:
        self.calls += 1
        if self.name == "escalate_to_expert":
            data = {"ticket_id": 42, "status": "open"}
            return {"ok": True, "data": data, **data}
        if self.name == "defect_case_search":
            defects = [{"id": "BUG-ANR-063", "module": "App"}]
            return {"ok": True, "data": {"count": 1, "defects": defects},
                    "count": 1, "defects": defects}
        return {"ok": True, "data": {"count": 1, "samples": [{"id": 1}]},
                "count": 1, "samples": [{"id": 1}]}

    def fail(self, message: str, meta: dict | None = None) -> dict:
        return {"ok": False, "data": {}, "error": message, "meta": meta or {}}


class _ScriptedProvider(LLMProvider):
    def __init__(self, plan: AgentPlan, decisions: list[LLMDecision],
                 replans: list[AgentPlan] | None = None) -> None:
        self._plan = plan
        self._decisions = list(decisions)
        self._replans = list(replans or [])
        self.chat_calls = 0
        self.finalize_calls = 0

    def plan(self, messages: list[dict], tools: list[dict]) -> AgentPlan:
        return self._plan

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMDecision:
        self.chat_calls += 1
        return self._decisions.pop(0)

    def finalize(self, messages: list[dict]) -> LLMDecision:
        self.finalize_calls += 1
        return self.chat(messages, [])

    def replan(self, messages: list[dict], tools: list[dict],
               previous_plan: AgentPlan, reason: str) -> AgentPlan:
        return self._replans.pop(0) if self._replans else self._plan


def _tool_call(name: str, payload: dict) -> LLMDecision:
    return LLMDecision(
        type="tool_call",
        thought=f"call {name}",
        tool_calls=[ToolCall(id=f"call_{name}", name=name, input=payload)],
    )


def _plan(with_case: bool) -> AgentPlan:
    steps = [
        PlanStep(
            id="recall_strategy",
            goal="Recall applicable strategy.",
            allowed_tools=["recall_troubleshooting_strategy"],
            required_evidence=["strategy result"],
            exit_condition="Strategy observed.",
            fallback="Continue with other evidence.",
        ),
    ]
    if with_case:
        steps.append(PlanStep(
            id="collect_case",
            goal="Find historical cases.",
            allowed_tools=["defect_case_search"],
            required_evidence=["case result"],
            exit_condition="Case observed.",
            fallback="Explain that no case matched.",
        ))
    steps.append(PlanStep(
        id="conclude_or_escalate",
        goal="Conclude safely or hand off.",
        allowed_tools=["escalate_to_expert"],
        exit_condition="Final answer or handoff created.",
        fallback="State missing evidence.",
    ))
    return AgentPlan(
        goal="Answer the current request with available evidence.",
        decision_reason="Use strategy and the evidence required by the request.",
        evidence_gap=[],
        steps=steps,
    )


def _loop(provider: _ScriptedProvider) -> tuple[AgentLoop, dict[str, _FakeTool]]:
    tools = {
        name: _FakeTool(name)
        for name in (
            "recall_troubleshooting_strategy",
            "defect_case_search",
            "test_sop_search",
            "escalate_to_expert",
        )
    }
    loop = AgentLoop(provider)
    loop.tools = tools
    return loop, tools


class AgentLoopHandoffTest(unittest.TestCase):
    def test_completed_plan_can_replan_when_finalizer_requests_it(self):
        initial = _plan(with_case=True)
        initial.steps.pop()
        provider = _ScriptedProvider(
            initial,
            [
                _tool_call("recall_troubleshooting_strategy", {"query": "Bluetooth"}),
                _tool_call("defect_case_search", {"query": "Bluetooth"}),
                LLMDecision(
                    type="final",
                    thought="The case result needs one more conclusion step.",
                    content="需要重新规划后续判断。",
                    terminal_action="replan",
                ),
                LLMDecision(
                    type="final",
                    thought="The replanned conclusion step is complete.",
                    content="已根据历史案例给出下一步建议。",
                ),
            ],
            replans=[_plan(with_case=True)],
        )
        loop, tools = _loop(provider)

        result = loop.run("Find a related Bluetooth case.")

        self.assertFalse(result.handoff)
        self.assertEqual(tools["escalate_to_expert"].calls, 0)
        self.assertTrue(any(event["type"] == "terminal_action" and event["action"] == "replan"
                            for event in result.trace))
        self.assertTrue(any(event["type"] == "replan" for event in result.trace))

    def test_completed_plan_can_handoff_when_finalizer_requests_it(self):
        plan = _plan(with_case=True)
        plan.steps.pop()
        provider = _ScriptedProvider(
            plan,
            [
                _tool_call("recall_troubleshooting_strategy", {"query": "Bluetooth"}),
                _tool_call("defect_case_search", {"query": "Bluetooth"}),
                LLMDecision(
                    type="final",
                    thought="The evidence indicates a high-risk protocol issue.",
                    content="需要专家确认协议栈风险。",
                    terminal_action="handoff",
                ),
            ],
        )
        loop, tools = _loop(provider)

        result = loop.run("Find a related Bluetooth case.")

        self.assertTrue(result.handoff)
        self.assertEqual(tools["escalate_to_expert"].calls, 1)
        self.assertTrue(any(event["type"] == "terminal_action" and event["action"] == "handoff"
                            for event in result.trace))

    def test_all_completed_steps_use_dedicated_finalizer(self):
        plan = _plan(with_case=True)
        plan.steps.pop()
        provider = _ScriptedProvider(
            plan,
            [
                _tool_call("recall_troubleshooting_strategy", {"query": "Bluetooth"}),
                _tool_call("defect_case_search", {"query": "Bluetooth"}),
                LLMDecision(
                    type="final",
                    thought="All required evidence has been collected.",
                    content="A relevant historical case was found.",
                ),
            ],
        )
        loop, tools = _loop(provider)

        result = loop.run("Find a related Bluetooth case.")

        self.assertFalse(result.handoff)
        self.assertEqual(provider.finalize_calls, 1)
        self.assertEqual(tools["escalate_to_expert"].calls, 0)
        self.assertIn("historical case", result.reply)

    def test_completed_plan_instructs_model_to_generate_final_reply(self):
        plan = _plan(with_case=True)
        for step in plan.steps:
            step.status = "completed"

        messages = AgentLoop._with_plan_context(
            [{"role": "system", "content": "base prompt"}], plan, None,
        )

        self.assertIn("【收尾阶段】", messages[0]["content"])
        self.assertIn("不得直接调用工具", messages[0]["content"])
        self.assertIn("final；需要新证据则 replan", messages[0]["content"])

    def test_mock_history_lookup_finishes_after_case_result(self):
        provider = MockLLMProvider()
        messages = [
            {"role": "user", "content": "有没有类似 ANR 的历史缺陷案例?"},
            {"role": "tool", "name": "recall_troubleshooting_strategy", "result": {"count": 0}},
            {"role": "tool", "name": "defect_case_search", "result": {
                "defects": [{"id": "BUG-ANR-063", "module": "App", "symptom": "ANR", "root_cause": "sync IO"}],
            }},
        ]

        plan = provider.plan(messages, [])
        decision = provider.chat(messages, [{"name": "placeholder"}])

        self.assertEqual(plan.evidence_gap, [])
        self.assertEqual(decision.type, "final")
        self.assertIn("BUG-ANR-063", decision.content)

    def test_case_lookup_can_finish_without_handoff(self):
        provider = _ScriptedProvider(
            _plan(with_case=True),
            [
                _tool_call("recall_troubleshooting_strategy", {"query": "ANR cases"}),
                _tool_call("defect_case_search", {"query": "ANR cases"}),
                LLMDecision(
                    type="final",
                    thought="A matching historical case is enough for this lookup.",
                    content="Found BUG-ANR-063 as a relevant historical case.",
                ),
            ],
        )
        loop, tools = _loop(provider)

        result = loop.run("Are there similar ANR historical defect cases?")

        self.assertFalse(result.handoff)
        self.assertIsNone(result.ticket_id)
        self.assertEqual(provider.chat_calls, 3)
        self.assertEqual(tools["escalate_to_expert"].calls, 0)
        self.assertIn("BUG-ANR-063", result.reply)
        self.assertTrue(any(event["type"] == "final_guard" and event["ok"]
                            for event in result.trace))

    def test_successful_handoff_ends_without_duplicate_ticket(self):
        provider = _ScriptedProvider(
            _plan(with_case=False),
            [
                _tool_call("recall_troubleshooting_strategy", {"query": "need expert"}),
                _tool_call("escalate_to_expert", {"question": "need expert"}),
            ],
        )
        loop, tools = _loop(provider)

        result = loop.run("Please escalate this issue to an expert.")

        self.assertTrue(result.handoff)
        self.assertEqual(result.ticket_id, 42)
        self.assertEqual(provider.chat_calls, 2)
        self.assertEqual(tools["escalate_to_expert"].calls, 1)
        self.assertFalse(any(event.get("status") == "forced_handoff"
                             for event in result.trace))
        self.assertEqual(
            len([event for event in result.trace
                 if event["type"] == "tool_result" and event.get("tool") == "escalate_to_expert"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
