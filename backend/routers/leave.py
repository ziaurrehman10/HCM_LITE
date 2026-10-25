import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
import analytics as A
from db import get_db
from models import LeaveRequest, LeaveType, Employee
from core import current_user, require_roles, own_employee, today, audit, notify, to_dict
from rules import working_days

router = APIRouter(prefix="/leave", tags=["M5 Leave"])

class LeaveIn(BaseModel):
    leave_type_id: int; start_date: dt.date; end_date: dt.date; reason: str | None = None

def _validate(db, e, b: LeaveIn):
    lt = db.get(LeaveType, b.leave_type_id)
    if not lt: raise HTTPException(422, "Unknown leave type")
    if b.end_date < b.start_date: raise HTTPException(422, "end_date before start_date")
    _, weekend, hol = A.ctx(db)
    days = working_days(b.start_date, b.end_date, weekend, hol)       # skips weekends + holidays
    if days == 0: raise HTTPException(422, "Selected dates contain no working days")
    clash = db.query(LeaveRequest).filter(LeaveRequest.employee_id == e.id,
            LeaveRequest.status.in_(("pending", "approved")), LeaveRequest.start_date <= b.end_date,
            LeaveRequest.end_date >= b.start_date).first()
    if clash: raise HTTPException(422, "Overlaps an existing leave request")
    if lt.quota is not None:
        y = b.start_date.year
        used = db.query(func.coalesce(func.sum(LeaveRequest.days), 0)).filter(
            LeaveRequest.employee_id == e.id, LeaveRequest.leave_type_id == lt.id,
            LeaveRequest.status.in_(("pending", "approved")),
            LeaveRequest.start_date.between(dt.date(y, 1, 1), dt.date(y, 12, 31))).scalar()
        if used + days > lt.quota: raise HTTPException(422, f"Over balance: {lt.quota - used:g} {lt.name} day(s) left, {days} requested")
    return lt, days

@router.post("", status_code=201)
def apply(b: LeaveIn, db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    e = own_employee(db, u); lt, days = _validate(db, e, b)
    lr = LeaveRequest(employee_id=e.id, days=days, **b.model_dump()); db.add(lr); db.flush()
    audit(db, u, "create", "leave", lr.id, None, to_dict(lr)); db.commit()
    return to_dict(lr)

@router.get("")
def list_leave(status: str | None = None, employee_id: int | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    q = db.query(LeaveRequest)
    if u.role == "employee": employee_id = own_employee(db, u).id
    if employee_id: q = q.filter(LeaveRequest.employee_id == employee_id)
    if status: q = q.filter(LeaveRequest.status == status)
    nm = {e.id: e.name for e in db.query(Employee)}; tn = {t.id: t.name for t in db.query(LeaveType)}
    return [to_dict(l) | {"employee": nm.get(l.employee_id), "type": tn.get(l.leave_type_id)}
            for l in q.order_by(LeaveRequest.id.desc())]

@router.get("/balance")
def balance(employee_id: int | None = None, year: int | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    if u.role == "employee": employee_id = own_employee(db, u).id
    if not employee_id: raise HTTPException(422, "employee_id required")
    return A.leave_balance(db, employee_id, year)

@router.post("/{lid}/cancel")
def cancel(lid: int, db: Session = Depends(get_db), u=Depends(require_roles("hr", "employee"))):
    e = own_employee(db, u); lr = db.get(LeaveRequest, lid)
    if not lr or lr.employee_id != e.id: raise HTTPException(404, "Not found")
    if lr.status not in ("pending", "approved") or lr.start_date < today(): raise HTTPException(422, "Cannot cancel this request")
    before = to_dict(lr); lr.status = "cancelled"
    audit(db, u, "cancel", "leave", lid, before, to_dict(lr)); db.commit(); return to_dict(lr)

class DecideIn(BaseModel): approve: bool; comment: str | None = None

@router.post("/{lid}/decide")
def decide(lid: int, b: DecideIn, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    lr = db.get(LeaveRequest, lid)
    if not lr: raise HTTPException(404, "Not found")
    if lr.status != "pending": raise HTTPException(409, "Already decided")
    if lr.employee_id == getattr(db.query(Employee).filter(Employee.user_id == hr.id).first(), "id", None):
        raise HTTPException(403, "You cannot approve your own request")
    before = to_dict(lr); lr.status = "approved" if b.approve else "rejected"
    lr.comment = b.comment; lr.decided_by = hr.id
    emp = db.get(Employee, lr.employee_id)
    notify(db, emp.user_id, f"Leave {lr.status}", f"{lr.start_date} to {lr.end_date}. {b.comment or ''}")
    audit(db, hr, "approve" if b.approve else "reject", "leave", lid, before, to_dict(lr)); db.commit()
    return to_dict(lr)
