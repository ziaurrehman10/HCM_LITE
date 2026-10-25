import os, json, secrets, datetime as dt
from zoneinfo import ZoneInfo
import bcrypt, jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from db import get_db
from models import User, Employee, AuditLog, Notification

SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
TZ = ZoneInfo(os.getenv("APP_TZ", "Asia/Karachi"))

def now(): return dt.datetime.now(TZ).replace(tzinfo=None)     # server-stamped local time
def today(): return now().date()

def hash_pw(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def verify_pw(p, h): return bcrypt.checkpw(p.encode(), h.encode())
def temp_password(): return secrets.token_urlsafe(9)

def make_token(u):
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)
    return jwt.encode({"sub": str(u.id), "role": u.role, "exp": exp}, SECRET, "HS256")

bearer = HTTPBearer(auto_error=False)
OPEN_WHILE_TEMP = {"/auth/change-password", "/auth/me"}

def current_user(request: Request, cred=Depends(bearer), db=Depends(get_db)):
    if not cred: raise HTTPException(401, "Not authenticated")
    try: p = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(401, "Invalid or expired token")
    u = db.get(User, int(p["sub"]))
    if not u or not u.is_active: raise HTTPException(401, "Account disabled")
    if u.must_change_password and request.url.path not in OPEN_WHILE_TEMP:
        raise HTTPException(403, "password_change_required")
    return u

def require_roles(*roles):
    def dep(u=Depends(current_user)):
        if u.role not in roles: raise HTTPException(403, "Forbidden for your role")
        return u
    return dep

def employee_of(db, u): return db.query(Employee).filter(Employee.user_id == u.id).first()

def own_employee(db, u):
    e = employee_of(db, u)
    if not e: raise HTTPException(404, "No employee profile linked to this login")
    return e

def to_dict(o, exclude=()):
    return {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name not in exclude}

def audit(db, actor, action, entity, entity_id=None, before=None, after=None):
    db.add(AuditLog(actor_id=actor.id if actor else None, actor_email=actor.email if actor else "system",
                    action=action, entity=entity, entity_id=str(entity_id) if entity_id is not None else None,
                    before=json.dumps(before, default=str) if before is not None else None,
                    after=json.dumps(after, default=str) if after is not None else None))

def notify(db, user_id, title, body=""):
    if user_id: db.add(Notification(user_id=user_id, title=title, body=body))

def next_code(db):
    return f"EMP-{db.query(Employee).count() + 1:04d}"
