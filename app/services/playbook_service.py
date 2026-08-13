"""策略样本库服务。

一条策略样本 = 测试专家给出的问题现象、排查路径和策略原因。
服务时按当前 ODM 问题做 embedding 语义检索,只召回最相关的 top-k 条样本交给
大模型精判并组织排查路径。总纲归纳仍取全量(recall_all)。
"""
from __future__ import annotations
import json
from typing import Any

from ..db import cursor
from ..knowledge import embedder

_LEGACY_COURSE_WORDS = (
    "\u8bfe\u7a0b", "\u5b66\u5458", "\u5c31\u4e1a", "\u85aa\u8d44",
    "\u4ef7\u683c", "\u62a5\u4ef7", "\u73ed\u4e3b\u4efb",
    "\u5305\u5c31\u4e1a", "\u5b66\u4e60\u610f\u5411",
    "\u54a8\u8be2\u987e\u95ee", "\u80fd\u5b66",
    "\u7814\u7a76\u751f", "\u5927\u4e13",
    "\u96f6\u57fa\u7840", "\u8fd9\u4e2a\u8bfe",
)


def _is_odm_sample(row: dict[str, Any]) -> bool:
    text = " ".join([
        row.get("question") or "",
        row.get("answer") or "",
        row.get("note") or "",
    ])
    return not any(word in text for word in _LEGACY_COURSE_WORDS)


def add_sample(question: str, answer: str, note: str = "",
               source: str = "taught") -> int:
    vector, sig = embedder.embed(question)
    with cursor() as cur:
        cur.execute(
            "INSERT INTO playbook (question, answer, note, source, vector, vec_model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (question, answer, note, source, json.dumps(vector), sig),
        )
        return cur.lastrowid


def list_samples(source: str | None = None) -> list[dict[str, Any]]:
    """列出策略样本;传入 source 时只返回该来源
    (taught 专家教学 / refine 专家纠错 / ticket 工单复盘)。"""
    with cursor() as cur:
        if source:
            cur.execute("SELECT * FROM playbook WHERE source=? ORDER BY id DESC", (source,))
        else:
            cur.execute("SELECT * FROM playbook ORDER BY id DESC")
        return [row for row in (dict(r) for r in cur.fetchall()) if _is_odm_sample(row)]


def recall_all() -> list[dict[str, Any]]:
    """召回全部样本(供策略总纲归纳用,需要看到所有示范)。"""
    with cursor() as cur:
        cur.execute("SELECT id, question, answer, note FROM playbook ORDER BY id")
        return [row for row in (dict(r) for r in cur.fetchall()) if _is_odm_sample(row)]


def _sample_vector(row: dict[str, Any], sig: str) -> list[float]:
    """取样本问句向量;缺失或后端签名不一致(切了 embedding 后端)则重算并回写。"""
    if row.get("vector") and row.get("vec_model") == sig:
        try:
            return json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError):
            pass
    vector, new_sig = embedder.embed(row["question"])
    with cursor() as cur:
        cur.execute("UPDATE playbook SET vector=?, vec_model=? WHERE id=?",
                    (json.dumps(vector), new_sig, row["id"]))
    return vector


def recall_topk(query: str, k: int) -> list[dict[str, Any]]:
    """按 ODM 问题语义检索,返回最相关的 top-k 条样本(按相似度降序,带 score)。

    query 为空时退化为全量返回,避免空检索导致答不上。
    """
    with cursor() as cur:
        cur.execute("SELECT id, question, answer, note, vector, vec_model FROM playbook ORDER BY id")
        rows = [row for row in (dict(r) for r in cur.fetchall()) if _is_odm_sample(row)]
    if not rows:
        return []
    if not (query or "").strip():
        return [{"id": r["id"], "question": r["question"], "answer": r["answer"],
                 "note": r["note"]} for r in rows]

    sig = embedder.signature()
    qvec, _ = embedder.embed(query)
    scored = []
    for r in rows:
        score = embedder.cosine(qvec, _sample_vector(r, sig))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"id": r["id"], "question": r["question"], "answer": r["answer"],
             "note": r["note"], "score": round(score, 3)}
            for score, r in scored[:k]]


def update_sample(sample_id: int, question: str, answer: str, note: str) -> dict[str, Any]:
    vector, sig = embedder.embed(question)
    with cursor() as cur:
        cur.execute(
            "UPDATE playbook SET question=?, answer=?, note=?, vector=?, vec_model=? WHERE id=?",
            (question, answer, note, json.dumps(vector), sig, sample_id),
        )
    return {"ok": True}


def delete_sample(sample_id: int) -> dict[str, Any]:
    with cursor() as cur:
        cur.execute("DELETE FROM playbook WHERE id=?", (sample_id,))
    return {"ok": True}


def count() -> int:
    with cursor() as cur:
        cur.execute("SELECT question, answer, note FROM playbook")
        return sum(1 for row in (dict(r) for r in cur.fetchall()) if _is_odm_sample(row))
