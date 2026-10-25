from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from db import get_db
from models import Announcement, Notification, AuditLog, User
from core import current_user, require_roles, audit, notify, to_dict

router = APIRouter(tags=["M9 Comms & Audit"])

class AnnIn(BaseModel): title: str = Field(min_length=2); body: str = Field(min_length=2)

@router.post("/announcements", status_code=201)
def post_announcement(b: AnnIn, db: Session = Depends(get_db), u=Depends(require_roles("admin", "hr"))):
    a = Announcement(author_id=u.id, **b.model_dump()); db.add(a); db.flush()
    for (uid,) in db.query(User.id).filter(User.is_active == True, User.id != u.id): notify(db, uid, f"Announcement: {a.title}", a.body)
    audit(db, u, "create", "announcement", a.id, None, to_dict(a)); db.commit(); return to_dict(a)

@router.get("/announcements")
def announcements(db: Session = Depends(get_db), u=Depends(current_user)):
    return [to_dict(a) for a in db.query(Announcement).order_by(Announcement.id.desc()).limit(50)]

@router.get("/notifications")
def notifications(unread: bool = False, db: Session = Depends(get_db), u=Depends(current_user)):
    q = db.query(Notification).filter_by(user_id=u.id)           # own only
    if unread: q = q.filter_by(is_read=False)
    return [to_dict(n) for n in q.order_by(Notification.id.desc()).limit(100)]

@router.post("/notifications/{nid}/read")
def read(nid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    n = db.get(Notification, nid)
    if not n or n.user_id != u.id: raise HTTPException(404, "Not found")
    n.is_read = True; db.commit(); return {"ok": True}

@router.get("/audit-log")
def audit_log(entity: str | None = None, action: str | None = None, actor: str | None = None,
              page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_db), u=Depends(require_roles("admin"))):
    q = db.query(AuditLog)
    if entity: q = q.filter(AuditLog.entity == entity)
    if action: q = q.filter(AuditLog.action == action)
    if actor: q = q.filter(AuditLog.actor_email.ilike(f"%{actor}%"))
    return {"total": q.count(), "items": [to_dict(a) for a in q.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)]}
