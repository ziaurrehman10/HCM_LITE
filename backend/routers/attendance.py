import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import analytics as A
from db import get_db
from models import Attendance, Correction, Employee, Setting
from core import (current_user, require_roles, own_employee, now, today, audit, notify, to_dict)
from rules import calc_status, haversine_m

router = APIRouter(prefix="/attendance", tags=["M4 Attendance"])

class CheckIn(BaseModel):
    source: str = Field("web", pattern="^(web|camera)$")
    lat: float | None = None; lng: float | None = None; accuracy: float | None = None
    face_count: int | None = None        # camera mode: client-side face detection result

def _status(s, ci, co=None): return calc_status(ci, co, s.office_start, s.grace_min, s.half_day_hours)

@router.post("/check-in", status_code=201)
def check_in(b: CheckIn = CheckIn(), db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    e = own_employee(db, u); s = db.query(Setting).first(); dist = None
    if b.source == "camera":
        if b.face_count != 1: raise HTTPException(422, "Exactly one face must be detected")
        if b.lat is None or b.lng is None: raise HTTPException(422, "Location required")
        if b.accuracy is None or b.accuracy > s.max_accuracy_m: raise HTTPException(422, f"GPS accuracy worse than {s.max_accuracy_m:.0f} m")
        if s.office_lat is None: raise HTTPException(422, "Admin has not set the office location")
        dist = haversine_m(b.lat, b.lng, s.office_lat, s.office_lng)      # server decides
        if dist > s.radius_m: raise HTTPException(403, f"Outside office area ({dist:.0f} m away, limit {s.radius_m:.0f} m)")
    t = now()
    if db.query(Attendance).filter_by(employee_id=e.id, date=t.date()).first():
        raise HTTPException(409, "Already checked in today")
    a = Attendance(employee_id=e.id, date=t.date(), check_in=t, status=_status(s, t), source=b.source,
                   lat=b.lat, lng=b.lng, distance_m=dist)
    db.add(a); db.commit()
    return to_dict(a)

@router.post("/check-out")
def check_out(db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    e = own_employee(db, u); s = db.query(Setting).first(); t = now()
    a = db.query(Attendance).filter_by(employee_id=e.id, date=t.date()).first()
    if not a: raise HTTPException(404, "No check-in today")
    if a.check_out: raise HTTPException(409, "Already checked out")
    a.check_out = t; a.status = _status(s, a.check_in, t); db.commit()
    return to_dict(a)

@router.get("")
def records(date_from: dt.date | None = None, date_to: dt.date | None = None, employee_id: int | None = None,
            department_id: int | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    """Calendar (employee) and daily board (HR/Admin: date_from = date_to = day)."""
    t = today(); date_to = date_to or t; date_from = date_from or date_to.replace(day=1)
    if u.role == "employee": employee_id = own_employee(db, u).id
    emps = A.scoped_employees(db, department_id, employee_id)
    nm = {e.id: e.name for e in emps}
    rows = A.daily_records(db, emps, date_from, date_to)
    return [{**r, "name": nm[r["employee_id"]]} for r in rows]

class CorrIn(BaseModel):
    date: dt.date; check_in: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    check_out: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"); reason: str = Field(min_length=3)

@router.post("/corrections", status_code=201)
def request_correction(b: CorrIn, db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    e = own_employee(db, u)
    if b.date > today(): raise HTTPException(422, "Cannot correct a future date")
    c = Correction(employee_id=e.id, **b.model_dump()); db.add(c); db.flush()
    audit(db, u, "create", "correction", c.id, None, to_dict(c)); db.commit(); return to_dict(c)

@router.get("/corrections")
def list_corrections(status: str | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    q = db.query(Correction)
    if u.role == "employee": q = q.filter(Correction.employee_id == own_employee(db, u).id)
    if status: q = q.filter(Correction.status == status)
    nm = {e.id: e.name for e in db.query(Employee)}
    return [to_dict(c) | {"employee": nm.get(c.employee_id)} for c in q.order_by(Correction.id.desc())]

class DecideIn(BaseModel): approve: bool; comment: str | None = None

@router.post("/corrections/{cid}/decide")
def decide(cid: int, b: DecideIn, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    c = db.get(Correction, cid)
    if not c: raise HTTPException(404, "Not found")
    if c.status != "pending": raise HTTPException(409, "Already decided")
    before = to_dict(c); s = db.query(Setting).first()
    c.status = "approved" if b.approve else "rejected"; c.comment = b.comment; c.decided_by = hr.id
    if b.approve:
        ci = dt.datetime.combine(c.date, dt.time.fromisoformat(c.check_in))
        co = dt.datetime.combine(c.date, dt.time.fromisoformat(c.check_out)) if c.check_out else None
        a = db.query(Attendance).filter_by(employee_id=c.employee_id, date=c.date).first()
        if not a: a = Attendance(employee_id=c.employee_id, date=c.date); db.add(a)
        a.check_in, a.check_out, a.source, a.status = ci, co, "correction", _status(s, ci, co)
    emp = db.get(Employee, c.employee_id)
    notify(db, emp.user_id, f"Attendance correction {c.status}", f"{c.date}: {b.comment or ''}")
    audit(db, hr, "approve" if b.approve else "reject", "correction", cid, before, to_dict(c)); db.commit()
    return to_dict(c)
