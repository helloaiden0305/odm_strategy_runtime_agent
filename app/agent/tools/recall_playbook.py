"""策略召回工具:按当前 ODM 问题检索专家排查样本。"""
from __future__ import annotations
from typing import Any

from .base import Tool
from .schemas import RecallPlaybookInput
from ...services import playbook_service
from ... import config


class RecallPlaybookTool(Tool):
    name = "recall_troubleshooting_strategy"
    description = ("召回测试专家教过的 ODM 问题排查策略样本,包含问题现象、建议排查路径和策略原因。"
                   "回答任何测试/研发问题前优先调用,用于判断应按哪类专家排查路径推进。")
    input_model = RecallPlaybookInput

    def run(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        raw_samples = playbook_service.recall_topk(query, config.PLAYBOOK_TOP_K)
        samples = [
            s for s in raw_samples
            if s.get("score") is None or s.get("score", 0.0) >= config.PLAYBOOK_SCORE_THRESHOLD
        ]
        meta = {
            "threshold": config.PLAYBOOK_SCORE_THRESHOLD,
            "before_count": len(raw_samples),
            "after_count": len(samples),
            "low_confidence": len(raw_samples) > 0 and len(samples) == 0,
        }
        data = {"count": len(samples), "samples": samples}
        return self.ok(data=data, meta=meta, legacy=data)
