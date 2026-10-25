from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
import datetime as dt
from db import get_db
from models import User, Setting, LeaveType, Employee
from core import (hash_pw, verify_pw, make_token, temp_password, current_user, require_roles,
                  audit, to_dict, next_code, today)

router = APIRouter(prefix="/auth", tags=["M1 Auth"])
DEFAULT_LEAVE = [("Annual", 14, True), ("Sick", 8, True), ("Casual", 10, True), ("Unpaid", None, False)]

def bootstrap(db: Session, company: str, name: str, email: str, password: str):
    if db.query(User).filter(User.role == "admin").first():
        raise HTTPException(409, "Setup already completed")
    db.add(Setting(company_name=company))
    u = User(email=email.lower(), name=name, password_hash=hash_pw(password), role="admin")
    db.add(u)
    for n, q, p in DEFAULT_LEAVE: db.add(LeaveType(name=n, quota=q, paid=p))
    db.flush(); audit(db, u, "setup", "company", 1, None, {"company": company, "admin": email}); db.commit()
    return u

def user_out(u): return {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
                         "must_change_password": u.must_change_password}

class SetupIn(BaseModel):
    company_name: str = Field(min_length=2); admin_name: str = "Admin"
    email: EmailStr; password: str = Field(min_length=8)

class LoginIn(BaseModel): email: EmailStr; password: str
class PwIn(BaseModel): old_password: str; new_password: str = Field(min_length=8)
class HRIn(BaseModel):
    email: EmailStr; name: str; with_profile: bool = False
    department_id: int | None = None; basic_salary: float = 0

@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {"setup_done": db.query(User).filter(User.role == "admin").first() is not None}

@router.post("/setup", status_code=201)
def setup(b: SetupIn, db: Session = Depends(get_db)):
    u = bootstrap(db, b.company_name, b.admin_name, b.email, b.password)
    return {"token": make_token(u), "user": user_out(u)}

@router.post("/login")
def login(b: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.email == b.email.lower()).first()
    if not u or not u.is_active or not verify_pw(b.password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {"token": make_token(u), "user": user_out(u)}

@router.get("/me")
def me(u=Depends(current_user)): return user_out(u)

@router.post("/change-password")
def change_pw(b: PwIn, u=Depends(current_user), db: Session = Depends(get_db)):
    if not verify_pw(b.old_password, u.password_hash): raise HTTPException(400, "Old password is wrong")
    u.password_hash = hash_pw(b.new_password); u.must_change_password = False
    audit(db, u, "change_password", "user", u.id); db.commit()
    return {"ok": True}

@router.get("/users")
def users(db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
    return [user_out(u) | {"is_active": u.is_active} for u in db.query(User).order_by(User.id)]

@router.post("/users", status_code=201)
def create_hr(b: HRIn, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
    if db.query(User).filter(User.email == b.email.lower()).first(): raise HTTPException(409, "Email already used")
    pw = temp_password()
    u = User(email=b.email.lower(), name=b.name, role="hr", password_hash=hash_pw(pw), must_change_password=True)
    db.add(u); db.flush()
    if b.with_profile:        # lets HR use check-in / payslips as an employee too
        db.add(Employee(code=next_code(db), name=b.name, email=u.email, department_id=b.department_id,
                        join_date=today(), basic_salary=b.basic_salary, user_id=u.id))
    audit(db, a, "create", "user", u.id, None, {"email": u.email, "role": "hr"}); db.commit()
    return user_out(u) | {"temporary_password": pw}

def _target(db, uid):
    u = db.get(User, uid)
    if not u: raise HTTPException(404, "User not found")
    if u.role == "admin": raise HTTPException(403, "Admin accounts cannot be modified here")
    return u

@router.post("/users/{uid}/reset-password")
def reset(uid: int, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
    u = _target(db, uid); pw = temp_password()
    u.password_hash = hash_pw(pw); u.must_change_password = True
    audit(db, a, "reset_password", "user", uid); db.commit()
    return {"temporary_password": pw}

@router.post("/users/{uid}/{action}")
def toggle(uid: int, action: str, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
    if action not in ("deactivate", "activate"): raise HTTPException(404, "Unknown action")
    u = _target(db, uid); before = {"is_active": u.is_active}
    u.is_active = action == "activate"
    audit(db, a, action, "user", uid, before, {"is_active": u.is_active}); db.commit()
    return user_out(u) | {"is_active": u.is_active}
