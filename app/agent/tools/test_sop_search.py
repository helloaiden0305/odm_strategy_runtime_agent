"""测试 SOP 检索工具:根据 ODM 问题匹配排查规范。"""
from __future__ import annotations
from typing import Any

from .base import Tool
from .schemas import TestSopSearchInput
from ...data.sops import all_sops
from ...knowledge import embedding


class TestSopSearchTool(Tool):
    name = "test_sop_search"
    description = "检索测试 SOP、刷机流程、日志采集规范和常见故障排查步骤。"
    input_model = TestSopSearchInput

    def run(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        q = (query or "").lower()
        qvec = embedding.embed(query)
        best = None
        best_score = 0.0
        for sop in all_sops():
            text = " ".join([sop["name"], sop["category"], sop["audience"],
                             sop["outline"], sop["highlight"], " ".join(sop.get("keywords", []))])
            keyword_hits = sum(1 for k in sop.get("keywords", []) if k.lower() in q)
            score = embedding.cosine(qvec, embedding.embed(text)) + keyword_hits * 0.2
            if score > best_score:
                best_score, best = score, sop
        if best and best_score >= 0.06:
            data = {"found": True, "score": round(best_score, 3),
                    "sop": {k: best[k] for k in ("id", "name", "category", "scope",
                                                 "duration", "audience", "outline", "highlight")}}
            return self.ok(data=data, meta={"best_score": round(best_score, 3)}, legacy=data)
        data = {"found": False, "score": round(best_score, 3),
                "sops": [{"name": sop["name"], "scope": sop["scope"],
                          "audience": sop["audience"]} for sop in all_sops()]}
        return self.ok(data=data, meta={"best_score": round(best_score, 3)}, legacy=data)
