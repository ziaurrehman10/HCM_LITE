import csv, io, re, datetime as dt
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
import analytics as A
from db import get_db
from models import Employee, User, Department, Designation, LeaveRequest, Payslip, PayrollRun
from core import (current_user, require_roles, own_employee, employee_of, hash_pw, temp_password,
                  audit, to_dict, next_code, today)

router = APIRouter(prefix="/employees", tags=["M3 Employees"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

class EmpIn(BaseModel):
    name: str = Field(min_length=2); email: EmailStr; code: str | None = None
    phone: str | None = None; address: str | None = None; gender: str | None = None
    department_id: int; designation_id: int | None = None
    join_date: dt.date; basic_salary: float = Field(gt=0); status: str = "active"

class EmpUpdate(BaseModel):
    name: str | None = None; phone: str | None = None; address: str | None = None; gender: str | None = None
    department_id: int | None = None; designation_id: int | None = None
    exit_date: dt.date | None = None; status: str | None = None; basic_salary: float | None = Field(None, gt=0)

class SelfUpdate(BaseModel): phone: str | None = None; address: str | None = None

def create_employee(db: Session, actor, d: dict):
    if not db.get(Department, d["department_id"]): raise HTTPException(422, "Unknown department")
    if d.get("designation_id") and not db.get(Designation, d["designation_id"]): raise HTTPException(422, "Unknown designation")
    email = d["email"].lower()
    if db.query(User).filter(User.email == email).first() or db.query(Employee).filter(Employee.email == email).first():
        raise HTTPException(409, f"Email {email} already exists")
    code = d.get("code") or next_code(db)
    if db.query(Employee).filter(Employee.code == code).first(): raise HTTPException(409, f"Code {code} already exists")
    pw = temp_password()
    u = User(email=email, name=d["name"], role="employee", password_hash=hash_pw(pw), must_change_password=True)
    db.add(u); db.flush()
    e = Employee(**{**d, "email": email, "code": code, "user_id": u.id}); db.add(e); db.flush()
    audit(db, actor, "create", "employee", e.id, None, to_dict(e))
    return e, pw

@router.post("", status_code=201)
def register(b: EmpIn, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    e, pw = create_employee(db, hr, b.model_dump()); db.commit()
    return to_dict(e) | {"temporary_password": pw}      # shown once; employee must change on first sign-in

@router.get("")
def directory(q: str | None = None, department_id: int | None = None, status: str | None = None,
              page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
              db: Session = Depends(get_db), u=Depends(current_user)):
    emp_id = own_employee(db, u).id if u.role == "employee" else None   # data-scope guard
    return A.search_employees(db, q, department_id, status, page, size, emp_id)

@router.get("/me")
def my_profile(db: Session = Depends(get_db), u=Depends(current_user)): return to_dict(own_employee(db, u))

@router.patch("/me")
def edit_me(b: SelfUpdate, db: Session = Depends(get_db), u=Depends(require_roles("employee", "hr"))):
    e = own_employee(db, u); before = to_dict(e)
    for k, v in b.model_dump(exclude_unset=True).items(): setattr(e, k, v)
    audit(db, u, "update", "employee", e.id, before, to_dict(e)); db.commit(); return to_dict(e)

@router.get("/export")
def export_csv(db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    rows = A.search_employees(db, size=100)["items"]; out = io.StringIO()
    cols = ["code", "name", "email", "department", "designation", "join_date", "basic_salary", "phone", "gender", "status"]
    w = csv.DictWriter(out, cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=employees.csv"})

REQUIRED = ["code", "name", "email", "department", "join_date", "basic_salary"]

def validate_rows(db, rows):
    errors, valid = [], []
    depts = {d.name.lower(): d.id for d in db.query(Department)}
    desigs = {d.name.lower(): d.id for d in db.query(Designation)}
    codes = {c for (c,) in db.query(Employee.code)}; emails = {e for (e,) in db.query(Employee.email)}
    emails |= {e for (e,) in db.query(User.email)}
    for i, r in enumerate(rows, start=2):                     # row 1 = header
        err = lambda f, why: errors.append({"row": i, "field": f, "reason": why})
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        n0 = len(errors)
        for f in REQUIRED:
            if not r.get(f): err(f, "required")
        if r.get("code") in codes: err("code", "duplicate code")
        if r.get("email"):
            if not EMAIL_RE.match(r["email"]): err("email", "invalid email")
            elif r["email"].lower() in emails: err("email", "duplicate email")
        if r.get("department") and r["department"].lower() not in depts: err("department", "unknown department")
        if r.get("designation") and r["designation"].lower() not in desigs: err("designation", "unknown designation")
        try: jd = dt.date.fromisoformat(r.get("join_date", ""))
        except ValueError:
            jd = None
            if r.get("join_date"): err("join_date", "must be ISO date YYYY-MM-DD")
        try: sal = float(r.get("basic_salary", ""))
        except ValueError:
            sal = None
            if r.get("basic_salary"): err("basic_salary", "must be a number")
        if sal is not None and sal <= 0: err("basic_salary", "must be > 0")
        if len(errors) == n0:
            codes.add(r["code"]); emails.add(r["email"].lower())
            valid.append(dict(code=r["code"], name=r["name"], email=r["email"].lower(),
                              department_id=depts[r["department"].lower()],
                              designation_id=desigs.get(r.get("designation", "").lower()),
                              join_date=jd, basic_salary=sal, phone=r.get("phone") or None,
                              gender=r.get("gender") or None))
    return errors, valid

@router.post("/import")
async def import_csv(file: UploadFile = File(...), dry_run: bool = True, db: Session = Depends(get_db),
                     hr=Depends(require_roles("hr"))):
    rows = list(csv.DictReader(io.StringIO((await file.read()).decode("utf-8-sig"))))
    if not rows: raise HTTPException(422, "CSV has no data rows")
    errors, valid = validate_rows(db, rows)
    if dry_run: return {"dry_run": True, "total_rows": len(rows), "valid_rows": len(valid), "errors": errors}
    if errors: raise HTTPException(422, {"message": "Fix errors first (run with dry_run=true)", "errors": errors})
    created = []
    for v in valid:
        e, pw = create_employee(db, hr, v); created.append({"code": e.code, "email": e.email, "temporary_password": pw})
    db.commit()
    return {"dry_run": False, "created": created}

@router.get("/{eid}")
def employee_360(eid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    e = db.get(Employee, eid)
    if not e: raise HTTPException(404, "Not found")
    if u.role == "employee" and e.user_id != u.id: raise HTTPException(403, "Not your record")
    t = today(); start = t - dt.timedelta(30)
    last = (db.query(Payslip, PayrollRun.month).join(PayrollRun).filter(Payslip.employee_id == eid, PayrollRun.status == "final")
            .order_by(PayrollRun.month.desc()).first())
    return {"profile": A.search_employees(db, emp_id=eid)["items"][0],
            "attendance_30d": A.attendance_summary(db, start, t, emp_id=eid)["counts"],
            "leave_balance": A.leave_balance(db, eid),
            "last_payslip": (to_dict(last[0]) | {"month": last[1]}) if last else None}

@router.put("/{eid}")
def update(eid: int, b: EmpUpdate, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    e = db.get(Employee, eid)
    if not e: raise HTTPException(404, "Not found")
    before = to_dict(e)
    for k, v in b.model_dump(exclude_unset=True).items(): setattr(e, k, v)
    if e.status == "exited":
        e.exit_date = e.exit_date or today()
        if e.user_id: db.get(User, e.user_id).is_active = False          # leaver loses access
    audit(db, hr, "update", "employee", eid, before, to_dict(e)); db.commit()
    return to_dict(e)
