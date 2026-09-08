"""策略进化闭环服务:答不上 → 生成工单 → 专家复盘 → 进策略库 → 下次按策略处理。"""
from __future__ import annotations
from typing import Any

from ..db import cursor
from . import playbook_service

_LEGACY_COURSE_WORDS = (
    "\u8bfe\u7a0b", "\u5b66\u5458", "\u5c31\u4e1a", "\u85aa\u8d44",
    "\u4ef7\u683c", "\u62a5\u4ef7", "\u73ed\u4e3b\u4efb",
    "\u5305\u5c31\u4e1a", "\u5b66\u4e60\u610f\u5411",
    "\u54a8\u8be2\u987e\u95ee", "\u80fd\u5b66",
    "\u7814\u7a76\u751f", "\u5927\u4e13",
    "\u96f6\u57fa\u7840", "\u8fd9\u4e2a\u8bfe",
)


def _is_odm_ticket(row: dict[str, Any]) -> bool:
    text = " ".join([row.get("question") or "", row.get("context") or ""])
    return not any(word in text for word in _LEGACY_COURSE_WORDS)


# ---------- 工单(需要升级的问题) ----------

def list_tickets(status: str | None = "open") -> list[dict[str, Any]]:
    with cursor() as cur:
        if status:
            cur.execute("SELECT * FROM tickets WHERE status=? ORDER BY id DESC", (status,))
        else:
            cur.execute("SELECT * FROM tickets ORDER BY id DESC")
        return [row for row in (dict(r) for r in cur.fetchall()) if _is_odm_ticket(row)]


def teach_from_ticket(ticket_id: int, answer: str, note: str = "") -> dict[str, Any]:
    """针对工单补充"排查路径 + 策略原因" → 进入策略样本库 → 关闭工单。"""
    with cursor() as cur:
        cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        ticket = cur.fetchone()
        if not ticket:
            return {"ok": False, "error": "工单不存在"}
    sample_id = playbook_service.add_sample(ticket["question"], answer, note, source="ticket")
    with cursor() as cur:
        cur.execute("UPDATE tickets SET status='closed' WHERE id=?", (ticket_id,))
    return {
        "ok": True,
        "sample_id": sample_id,
        "sample_stats": playbook_service.sample_stats(),
        "message": "已保存至策略样本库，可用于后续策略总纲归纳。",
    }


# ---------- 统计看板 ----------

def metrics() -> dict[str, Any]:
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM metrics_log")
        total = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM metrics_log WHERE handoff=1")
        handoff = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM metrics_log WHERE hit_kb=1")
        hit = cur.fetchone()["c"]
    kb_count = playbook_service.count()
    return {
        "kb_count": kb_count,
        "total_chats": total,
        "handoff_count": handoff,
        "handoff_rate": round(handoff / total, 3) if total else 0.0,
        "kb_hit_count": hit,
        "kb_hit_rate": round(hit / total, 3) if total else 0.0,
    }
