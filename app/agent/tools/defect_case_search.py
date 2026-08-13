"""历史缺陷案例查询工具。"""
from __future__ import annotations
from typing import Any

from .base import Tool
from .schemas import DefectCaseSearchInput
from ...data.cases import all_cases


class DefectCaseSearchTool(Tool):
    name = "defect_case_search"
    description = ("检索历史缺陷案例、类似问题处理记录、量产/试产问题案例,返回问题现象、"
                   "根因和处理方式。需要参考相似案例时调用。")
    input_model = DefectCaseSearchInput

    def run(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        q = (query or "").lower()
        hits = [c for c in all_cases()
                if any(k.lower() in q for k in c.get("keywords", []))]
        if not hits:
            hits = all_cases()[:3]
        defects = [{"id": c["id"], "module": c["module"], "symptom": c["symptom"],
                    "root_cause": c["root_cause"], "resolution": c["resolution"]}
                   for c in hits[:4]]
        data = {"count": len(defects), "defects": defects}
        return self.ok(data=data, meta={"fallback": not bool(q)}, legacy=data)
