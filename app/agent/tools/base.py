"""工具统一接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Type

from pydantic import BaseModel, ValidationError


class Tool(ABC):
    name: str = ""
    description: str = ""
    input_model: Type[BaseModel] | None = None
    # 入参 JSON Schema,供真实 LLM 做 function calling;默认单一 query 参数
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "用户问题原文"},
        },
        "required": ["query"],
    }

    @abstractmethod
    def run(self, **kwargs: Any) -> dict[str, Any]:
        """执行工具,返回结构化结果(dict)。"""
        raise NotImplementedError

    def validate_input(self, payload: Any) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
        """校验模型传入的工具参数,失败时返回结构化错误信息。"""
        if not isinstance(payload, dict):
            return False, {}, {
                "message": "工具入参必须是 object",
                "fields": [{"loc": [], "msg": f"收到 {type(payload).__name__}"}],
            }
        if self.input_model is None:
            return True, payload, None
        try:
            model = self.input_model.model_validate(payload)
        except ValidationError as exc:
            return False, {}, {
                "message": "工具入参校验失败",
                "fields": [
                    {"loc": list(err.get("loc", ())), "msg": err.get("msg", "")}
                    for err in exc.errors()
                ],
            }
        return True, model.model_dump(), None

    def ok(self, data: dict[str, Any] | None = None,
           meta: dict[str, Any] | None = None,
           legacy: dict[str, Any] | None = None) -> dict[str, Any]:
        """返回统一工具结果,同时允许保留旧字段兼容前端。"""
        data = data or {}
        result = {
            "ok": True,
            "tool": self.name,
            "data": data,
            "error": None,
            "meta": meta or {},
        }
        if legacy:
            result.update(legacy)
        return result

    def fail(self, error: str, meta: dict[str, Any] | None = None,
             data: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": self.name,
            "data": data or {},
            "error": error,
            "meta": meta or {},
        }

    def schema(self) -> dict[str, Any]:
        """暴露给(真实)LLM 的工具描述。"""
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}
