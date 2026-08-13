"""FastAPI 入口:ODM 策略验证、专家教学、工单、统计,以及前端静态页。"""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .models import (ChatRequest, ChatResponse, TeachRequest,
                     TicketTeachRequest, SampleUpdate, DirectiveUpdate,
                     SummaryUpdate, RefineRequest, RefineCommitRequest)
from .services import (chat_service, review_service, playbook_service,
                       settings_service)

app = FastAPI(title="XZY ODM 问题排查策略自进化平台")


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------- 策略验证 ----------

@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    result = chat_service.handle_chat(req.message, req.session_id or "demo")
    return ChatResponse(
        reply=result.reply,
        handoff=result.handoff,
        ticket_id=result.ticket_id,
        trace=result.trace,
    )


@app.post("/api/session/reset")
def reset_session(session_id: str = "demo") -> dict:
    chat_service.reset_session(session_id)
    return {"ok": True}


# ---------- 专家纠错 ----------

@app.post("/api/refine")
def refine(req: RefineRequest) -> dict:
    """据专家点评,把 AI 刚才这条回复当场重答一遍。"""
    revised = chat_service.refine_reply(req.question, req.reply, req.feedback, req.session_id)
    return {"reply": revised}


@app.post("/api/refine/commit")
def refine_commit(req: RefineCommitRequest) -> dict:
    """把纠错结果固化为策略样本,并自动合并进总纲。"""
    return chat_service.commit_refinement(req.question, req.answer, req.feedback)


# ---------- 专家教学 / 策略样本库 ----------

@app.post("/api/teach")
def teach(req: TeachRequest) -> dict:
    sample_id = playbook_service.add_sample(req.question, req.answer, req.note)
    return {"ok": True, "sample_id": sample_id}


@app.get("/api/playbook")
def get_playbook(source: str | None = None) -> list[dict]:
    """策略样本列表;可选 source 过滤:taught(专家教学)/ refine(专家纠错)/ ticket(工单复盘)。"""
    return playbook_service.list_samples(source)


@app.get("/api/playbook/summary")
def playbook_summary() -> dict:
    """排查策略总纲:返回已保存版本(可被专家改写);首次访问自动归纳并保存。"""
    return {"summary": chat_service.get_or_build_summary()}


@app.put("/api/playbook/summary")
def save_playbook_summary(req: SummaryUpdate) -> dict:
    """测试专家手动改写总纲并保存。"""
    return settings_service.set_summary(req.text)


@app.post("/api/playbook/summary/regenerate")
def regenerate_playbook_summary() -> dict:
    """状态合并:保留专家改写,融合全部样本重新归纳。"""
    return {"summary": chat_service.regenerate_summary()}


@app.put("/api/playbook/{sample_id}")
def update_playbook(sample_id: int, req: SampleUpdate) -> dict:
    return playbook_service.update_sample(sample_id, req.question, req.answer, req.note)


@app.delete("/api/playbook/{sample_id}")
def delete_playbook(sample_id: int) -> dict:
    return playbook_service.delete_sample(sample_id)


# ---------- 工单(需要升级的问题) → 专家复盘 ----------

@app.get("/api/tickets")
def get_tickets(status: str | None = "open") -> list[dict]:
    return review_service.list_tickets(status)


@app.post("/api/tickets/{ticket_id}/teach")
def teach_ticket(ticket_id: int, req: TicketTeachRequest) -> dict:
    return review_service.teach_from_ticket(ticket_id, req.answer, req.note)


# ---------- AI 业务设定(全局角色/语气/铁律) ----------

@app.get("/api/settings/directive")
def get_directive() -> dict:
    return {"directive": settings_service.get_directive()}


@app.put("/api/settings/directive")
def put_directive(req: DirectiveUpdate) -> dict:
    return settings_service.set_directive(req.text)


@app.get("/api/settings/system-prompt")
def get_system_prompt() -> dict:
    """完整生效的系统提示词(技术流程 + 业务设定),供专家查看。"""
    return {"prompt": chat_service.effective_system_prompt()}


# ---------- 统计 ----------

@app.get("/api/metrics")
def get_metrics() -> dict:
    return review_service.metrics()


# ---------- 前端 ----------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
