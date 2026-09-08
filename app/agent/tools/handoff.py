"""升级测试专家工具:生成问题工单,作为专家复盘闭环的起点。"""
from __future__ import annotations
from typing import Any, Callable

from .base import Tool
from .schemas import HandoffInput
from ...db import cursor


class HandoffTool(Tool):
    name = "escalate_to_expert"
    description = "当证据不足、风险较高或需要人工判断时,生成问题工单并升级测试专家。"
    input_model = HandoffInput
    parameters = {
        "type": "object",
            "properties": {
            "question": {"type": "string", "description": "需要升级的问题原文"},
            "context": {"type": "string", "description": "升级原因/上下文说明"},
        },
        "required": ["question"],
    }

    def run(self, question: str = "", context: str = "",
            _should_cancel: Callable[[], bool] | None = None,
            **kwargs: Any) -> dict[str, Any]:
        if _should_cancel and _should_cancel():
            return self.fail("本轮策略验证已中止")
        with cursor() as cur:
            if _should_cancel and _should_cancel():
                return self.fail("本轮策略验证已中止")
            cur.execute(
                "INSERT INTO tickets (question, context, status) VALUES (?, ?, 'open')",
                (question, context),
            )
            ticket_id = cur.lastrowid
        data = {"ticket_id": ticket_id, "status": "open"}
        return self.ok(data=data, legacy=data)
