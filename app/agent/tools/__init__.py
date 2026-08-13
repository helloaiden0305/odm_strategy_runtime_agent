"""工具注册表。新增工具 → 在这里登记即可被 Agent Loop 使用。"""
from .recall_playbook import RecallPlaybookTool
from .test_sop_search import TestSopSearchTool
from .defect_case_search import DefectCaseSearchTool
from .handoff import HandoffTool


def build_registry() -> dict:
    tools = [
        RecallPlaybookTool(),
        TestSopSearchTool(),
        DefectCaseSearchTool(),
        HandoffTool(),
    ]
    return {t.name: t for t in tools}
