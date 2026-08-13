"""SQLite 初始化与轻量读写封装。"""
import sqlite3
import json
from contextlib import contextmanager
from typing import Iterator

from . import config
from .knowledge import embedder


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                context TEXT,
                status TEXT NOT NULL DEFAULT 'open',  -- open | answered | closed
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                vector TEXT NOT NULL,                  -- JSON 序列化的稀疏向量
                source TEXT NOT NULL DEFAULT 'seed',   -- seed | human
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS playbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,                 -- 客户问题(示范)
                answer TEXT NOT NULL,                   -- 标准答案/话术
                note TEXT,                              -- 这么答的原因/套路
                source TEXT NOT NULL DEFAULT 'taught',  -- taught(对话教) | ticket(工单补充)
                vector TEXT,                            -- 问题的向量(JSON list[float]),供 top-k 语义检索
                vec_model TEXT,                         -- 生成该向量的后端签名,后端切换时据此重算
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        # 兼容旧库:老 playbook 表可能没有向量列,缺失则补上(不影响已有数据)
        existing_cols = {row["name"] for row in cur.execute("PRAGMA table_info(playbook)")}
        if "vector" not in existing_cols:
            cur.execute("ALTER TABLE playbook ADD COLUMN vector TEXT")
        if "vec_model" not in existing_cols:
            cur.execute("ALTER TABLE playbook ADD COLUMN vec_model TEXT")
        # 兼容旧库:早期「实测纠偏」样本混存为 source='taught'(仅靠 note 前缀区分),
        # 迁移为独立的 source='refine',使教学套路与纠错数据可分开管理与展示。
        cur.execute(
            "UPDATE playbook SET source='refine' "
            "WHERE source='taught' AND note LIKE '(实测纠偏)%'"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                question TEXT,
                hit_kb INTEGER NOT NULL DEFAULT 0,
                handoff INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
            """
        )
        _ensure_odm_demo_samples(cur)
        if config.RESET_DEMO_RUNTIME_ON_START:
            _reset_demo_runtime(cur)


_DEMO_PLAYBOOK = [
    (
        "蓝牙耳机偶现连接失败,应该怎么排查?",
        "问题判断:先按蓝牙连接链路异常处理,不要直接判硬件。需要补充设备型号、固件版本、耳机型号、是否稳定复现和 bt_stack 日志。建议先复现并确认是否只发生在特定协议或设备组合,再查蓝牙 SOP 和历史缺陷;证据不足或疑似协议栈问题时升级测试专家。",
        "蓝牙类问题先补齐复现环境和协议栈日志,再判断兼容性、固件或硬件风险。",
    ),
    (
        "刷机失败一直卡在 20%,可能是什么原因?",
        "问题判断:优先按刷机/升级链路异常处理。先核对版本包、签名、分区空间、线材/端口和失败日志,确认是否固定卡在同一进度和同一批次设备;再检查 bootloader、分区写入和包校验记录。",
        "刷机失败不能只看进度百分比,需要结合失败码、包信息和设备环境定位。",
    ),
    (
        "稳定性测试压测 2 小时后 App 卡死,下一步查什么?",
        "问题判断:按稳定性/ANR 类问题处理。先保留复现脚本、压测时长、设备温度、CPU/内存曲线,采集 bugreport、logcat 和 traces;重点确认主线程是否阻塞、是否有内存泄漏或 IO 堆积。",
        "稳定性问题必须先保留现场和关键日志,再进入模块归因。",
    ),
]


def _ensure_odm_demo_samples(cur: sqlite3.Cursor) -> None:
    """补充脱敏 ODM 演示策略样本;不删除或覆盖本地已有记录。"""
    for question, answer, note in _DEMO_PLAYBOOK:
        cur.execute("SELECT 1 FROM playbook WHERE question=? LIMIT 1", (question,))
        if cur.fetchone():
            continue
        vector, sig = embedder.embed(question)
        cur.execute(
            "INSERT INTO playbook (question, answer, note, source, vector, vec_model) "
            "VALUES (?, ?, ?, 'taught', ?, ?)",
            (question, answer, note, json.dumps(vector), sig),
        )


def _reset_demo_runtime(cur: sqlite3.Cursor) -> None:
    """清理 Demo 运行态记录,保留 playbook/settings 等可复用策略资产。"""
    cur.execute("DELETE FROM tickets")
    cur.execute("DELETE FROM metrics_log")
