"""业务设定:测试专家可在后台维护的全局规则,会注入系统提示词。"""
from __future__ import annotations

from ..db import cursor

_KEY = "business_directive"
_SUMMARY_KEY = "playbook_summary"


def _get(key: str, default: str = "") -> str:
    with cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
    return row["value"] if row else default


def _set(key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


_LEGACY_BUSINESS_WORDS = (
    "\u8bfe\u7a0b", "\u5b66\u5458", "\u5c31\u4e1a", "\u85aa\u8d44",
    "\u4ef7\u683c", "\u62a5\u4ef7", "\u73ed\u4e3b\u4efb",
    "\u5305\u5c31\u4e1a", "\u5b66\u4e60\u610f\u5411",
    "\u54a8\u8be2\u987e\u95ee", "\u80fd\u5b66",
    "\u7814\u7a76\u751f", "\u5927\u4e13",
    "\u96f6\u57fa\u7840", "\u8fd9\u4e2a\u8bfe",
)


def _has_legacy_business_residue(text: str) -> bool:
    return any(word in (text or "") for word in _LEGACY_BUSINESS_WORDS)


DEFAULT_DIRECTIVE = (
    "【语气】回复克制、清晰、可执行,优先给排查路径,避免直接下结论。\n"
    "【铁律】\n"
    "1. 证据不足时先追问复现条件、版本、设备型号、日志和操作路径。\n"
    "2. 疑似底层协议、固件、硬件或高风险量产问题时,必须建议升级测试专家。\n"
    "3. 事实判断必须基于测试 SOP、日志规范或历史缺陷案例,不能编造根因。\n"
    "4. 输出尽量包含问题判断、需补充信息、排查步骤、参考案例和是否建议升级。"
)


def get_directive() -> str:
    value = _get(_KEY, DEFAULT_DIRECTIVE)
    return DEFAULT_DIRECTIVE if _has_legacy_business_residue(value) else value


def set_directive(text: str) -> dict:
    _set(_KEY, text)
    return {"ok": True}


def get_summary() -> str:
    """已保存的排查策略总纲;空串表示还没生成过。"""
    value = _get(_SUMMARY_KEY, "")
    return "" if _has_legacy_business_residue(value) else value


def set_summary(text: str) -> dict:
    _set(_SUMMARY_KEY, text)
    return {"ok": True}
