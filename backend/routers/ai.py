import json, time, datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import ai_service as S
import ml_model
from db import get_db
from models import AiLog
from core import current_user, require_roles, today
from routers.dashboard import build

router = APIRouter(prefix="/ai", tags=["M8 AI"])
FALLBACK = "The AI assistant is unavailable right now. Please use the dashboards or try again shortly."
LIMIT_PER_HOUR = 20

def _guard(db, u):
    since = dt.datetime.utcnow() - dt.timedelta(hours=1)
    if db.query(AiLog).filter(AiLog.user_id == u.id, AiLog.created_at >= since).count() >= LIMIT_PER_HOUR:
        raise HTTPException(429, "AI rate limit reached (20 requests/hour)")

def _log(db, u, kind, prompt, calls, resp, status, t0):
    db.add(AiLog(user_id=u.id, kind=kind, prompt=prompt[:2000], tool_calls=json.dumps(calls, default=str),
                 response=(resp or "")[:4000], status=status, latency_ms=int((time.monotonic() - t0) * 1000))); db.commit()

class Turn(BaseModel): role: str = Field(pattern="^(user|assistant)$"); content: str = Field(max_length=2000)
class ChatIn(BaseModel): message: str = Field(min_length=1, max_length=1000); history: list[Turn] = []

@router.post("/chat")
def chat(b: ChatIn, db: Session = Depends(get_db), u=Depends(current_user)):
    _guard(db, u); t0 = time.monotonic()
    try:
        ans, calls = S.chat(db, u, b.message, [t.model_dump() for t in b.history[-6:]])
        _log(db, u, "chat", b.message, calls, ans, "ok", t0)
        return {"answer": ans, "tools_used": [c["tool"] for c in calls], "ai_generated": True}
    except S.AIError as ex:
        _log(db, u, "chat", b.message, [], str(ex), "error", t0)
        return {"answer": FALLBACK, "ai_generated": True, "error": True}

class SumIn(BaseModel):
    date_from: dt.date | None = None; date_to: dt.date | None = None; department_id: int | None = None

@router.post("/summarize")
def summarize(b: SumIn, db: Session = Depends(get_db), u=Depends(require_roles("admin", "hr"))):
    _guard(db, u); t0 = time.monotonic(); t = today()
    data = build(db, u, b.date_from or t.replace(day=1), b.date_to or t, b.department_id)   # recomputed server-side
    for k in ("announcements", "audit_feed"): data.pop(k, None)
    try:
        txt = S.summarize(data); _log(db, u, "summary", "dashboard", [], txt, "ok", t0)
        return {"summary": txt, "ai_generated": True}
    except S.AIError as ex:
        _log(db, u, "summary", "dashboard", [], str(ex), "error", t0)
        return {"summary": FALLBACK, "ai_generated": True, "error": True}

class LeaveText(BaseModel): text: str = Field(min_length=3, max_length=500)

@router.post("/smart-leave")
def smart_leave(b: LeaveText, db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    _guard(db, u); t0 = time.monotonic()
    try:
        form = S.smart_leave(db, b.text); _log(db, u, "smart_leave", b.text, [], json.dumps(form, default=str), "ok", t0)
        return {"form": form, "ai_generated": True, "note": "Review and confirm - nothing has been saved."}
    except S.AIError as ex:
        _log(db, u, "smart_leave", b.text, [], str(ex), "error", t0)
        return {"form": None, "fallback": True, "message": "Couldn't parse that - please fill the leave form manually."}

@router.get("/attrition-risk")
def attrition_risk(department_id: int | None = None, db: Session = Depends(get_db), u=Depends(require_roles("admin", "hr"))):
    return ml_model.score_employees(db, department_id)
