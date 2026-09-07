"""专家确认总纲保护性合并的隔离验收。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TEMP_DIR.name) / "summary_guard.db")
os.environ["RESET_DEMO_RUNTIME_ON_START"] = "false"

from app.db import init_db
from app.llm.base import LLMDecision
from app.llm.mock_provider import MockLLMProvider
from app.services import chat_service, settings_service


class _SummaryProvider:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return LLMDecision(
            type="final",
            thought="测试归纳结果。",
            content=self.responses.pop(0),
        )


class SummaryMergeGuardTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.original_provider = chat_service._LOOP.llm
        settings_service.set_summary("", status=settings_service.SUMMARY_DRAFT)

    def tearDown(self):
        chat_service._LOOP.llm = self.original_provider

    def test_first_summary_is_draft(self):
        chat_service._LOOP.llm = MockLLMProvider()
        result = chat_service.get_or_build_summary()

        self.assertTrue(result.summary)
        self.assertEqual(result.merge_status, "draft_generated")
        self.assertEqual(result.summary_status, settings_service.SUMMARY_DRAFT)

    def test_confirmed_base_is_preserved_after_retry(self):
        base = "1. 专家确认：必须先核对版本和关键日志。"
        settings_service.set_summary(base, status=settings_service.SUMMARY_EXPERT_CONFIRMED)
        chat_service._LOOP.llm = _SummaryProvider([
            "1. 被模型改写的规则。",
            base + "\n\n补充策略\n2. 对比 OTA 前后版本差异。",
        ])

        result = chat_service.regenerate_summary()

        self.assertEqual(result.merge_status, "merged_after_retry")
        self.assertTrue(result.summary.startswith(base))
        self.assertEqual(settings_service.get_summary(), result.summary)
        self.assertEqual(settings_service.get_summary_status(), settings_service.SUMMARY_EXPERT_CONFIRMED)

    def test_unsafe_candidates_never_overwrite_confirmed_base(self):
        base = "1. 专家确认：证据不足时不得直接下根因结论。"
        settings_service.set_summary(base, status=settings_service.SUMMARY_EXPERT_CONFIRMED)
        chat_service._LOOP.llm = _SummaryProvider([
            "模型重写了原规则。",
            "模型第二次仍未保留原规则。",
        ])

        result = chat_service.regenerate_summary()

        self.assertEqual(result.merge_status, "protected_existing")
        self.assertEqual(result.summary, base)
        self.assertEqual(settings_service.get_summary(), base)

    def test_mock_keeps_confirmed_base(self):
        base = "1. 专家确认：蓝牙异常先采集 bt_stack 日志。"
        settings_service.set_summary(base, status=settings_service.SUMMARY_EXPERT_CONFIRMED)
        chat_service._LOOP.llm = MockLLMProvider()

        result = chat_service.regenerate_summary()

        self.assertEqual(result.summary, base)
        self.assertEqual(result.summary_status, settings_service.SUMMARY_EXPERT_CONFIRMED)


if __name__ == "__main__":
    unittest.main()
