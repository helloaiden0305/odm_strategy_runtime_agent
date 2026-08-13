"""工具入参模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class RecallPlaybookInput(_StrictInput):
    query: str = Field(..., min_length=1, description="ODM 问题原文")


class TestSopSearchInput(_StrictInput):
    query: str = Field(..., min_length=1, description="ODM 问题原文")


class DefectCaseSearchInput(_StrictInput):
    query: str = Field(..., min_length=1, description="ODM 问题原文")


class HandoffInput(_StrictInput):
    question: str = Field(..., min_length=1, description="需要升级测试专家的问题原文")
    context: str = Field("", description="升级原因/上下文说明")
