import io, datetime as dt
from collections import Counter, defaultdict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import analytics as A
from db import get_db
from models import PayrollRun, Payslip, Employee
from core import current_user, require_roles, own_employee, now, today, audit, notify, to_dict
from rules import compute_payslip, month_bounds, working_days

router = APIRouter(prefix="/payroll", tags=["M6 Payroll"])

class SalaryIn(BaseModel): basic_salary: float = Field(gt=0)

@router.put("/salary/{eid}")
def set_salary(eid: int, b: SalaryIn, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    e = db.get(Employee, eid)
    if not e: raise HTTPException(404, "Not found")
    before = {"basic_salary": e.basic_salary}; e.basic_salary = b.basic_salary
    audit(db, hr, "update", "salary", eid, before, {"basic_salary": b.basic_salary}); db.commit()
    return {"employee_id": eid, "basic": b.basic_salary, "hra": b.basic_salary * .4, "medical": b.basic_salary * .1,
            "gross": b.basic_salary * 1.5}

@router.get("/salary/{eid}")
def get_salary(eid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    e = db.get(Employee, eid)
    if not e: raise HTTPException(404, "Not found")
    if u.role == "employee" and e.user_id != u.id: raise HTTPException(403, "Not your record")
    b = e.basic_salary
    return {"employee_id": eid, "basic": b, "hra": b * .4, "medical": b * .1, "gross": b * 1.5}

def _build(db, run, ms, me):
    c = A.ctx(db); _, weekend, hol = c
    wd_month = working_days(ms, me, weekend, hol)
    emps = db.query(Employee).filter(Employee.join_date <= me, (Employee.exit_date.is_(None)) | (Employee.exit_date >= ms),
                                     Employee.basic_salary > 0).all()
    cnt = defaultdict(Counter)
    for r in A.daily_records(db, emps, ms, me): cnt[r["employee_id"]][r["status"]] += 1
    lv = A.leave_map(db, [e.id for e in emps], ms, me, c)
    for e in emps:
        lo = max(ms, e.join_date); hi = min(me, e.exit_date) if e.exit_date else me
        unpaid = sum(1 for d, (_, paid) in lv[e.id].items() if not paid and lo <= d <= hi)
        p = compute_payslip(e.basic_salary, wd_month, working_days(lo, hi, weekend, hol),
                            cnt[e.id]["absent"], unpaid, cnt[e.id]["half_day"])
        db.add(Payslip(run_id=run.id, employee_id=e.id, department_id=e.department_id, **p))
    return len(emps)

class RunIn(BaseModel): month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

@router.post("/runs", status_code=201)
def create_draft(b: RunIn, db: Session = Depends(get_db), hr=Depends(require_roles("hr"))):
    ms, me = month_bounds(b.month)
    if ms > today(): raise HTTPException(422, "Cannot run payroll for a future month")
    run = db.query(PayrollRun).filter_by(month=b.month).first()
    if run and run.status == "final": raise HTTPException(409, "Payroll for this month is finalized and locked")
    if run: db.query(Payslip).filter_by(run_id=run.id).delete()      # regenerate draft
    else: run = PayrollRun(month=b.month, created_by=hr.id); db.add(run); db.flush()
    n = _build(db, run, ms, me)
    audit(db, hr, "create", "payroll_run", run.id, None, {"month": b.month, "employees": n}); db.commit()
    return to_dict(run) | {"employees": n}

@router.get("/runs")
def runs(db: Session = Depends(get_db), u=Depends(require_roles("admin", "hr"))):
    return [to_dict(r) for r in db.query(PayrollRun).order_by(PayrollRun.month.desc())]

def _slip_out(db, p):
    run = db.get(PayrollRun, p.run_id); e = db.get(Employee, p.employee_id)
    return to_dict(p) | {"month": run.month, "run_status": run.status, "employee": e.name, "code": e.code}

@router.get("/runs/{rid}")
def run_detail(rid: int, db: Session = Depends(get_db), u=Depends(require_roles("admin", "hr"))):
    run = db.get(PayrollRun, rid)
    if not run: raise HTTPException(404, "Not found")
    slips = db.query(Payslip).filter_by(run_id=rid).all()
    return {"run": to_dict(run), "totals": {"gross": round(sum(s.gross for s in slips), 2),
            "net": round(sum(s.net for s in slips), 2), "tax": round(sum(s.tax for s in slips), 2)},
            "payslips": [_slip_out(db, s) for s in slips]}

@router.post("/runs/{rid}/finalize")
def finalize(rid: int, db: Session = Depends(get_db), admin=Depends(require_roles("admin"))):
    run = db.get(PayrollRun, rid)
    if not run: raise HTTPException(404, "Not found")
    if run.status == "final": raise HTTPException(409, "Already finalized")
    if not db.query(Payslip).filter_by(run_id=rid).count(): raise HTTPException(422, "Draft has no payslips")
    before = to_dict(run); run.status, run.finalized_by, run.finalized_at = "final", admin.id, now()
    for s in db.query(Payslip).filter_by(run_id=rid):
        notify(db, db.get(Employee, s.employee_id).user_id, "Payslip available", f"Payslip for {run.month} is ready.")
    audit(db, admin, "finalize", "payroll_run", rid, before, to_dict(run)); db.commit()
    return to_dict(run)

@router.get("/payslips")
def payslips(month: str | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    q = db.query(Payslip).join(PayrollRun)
    if u.role == "employee":     # employees see only their own, finalized payslips
        q = q.filter(Payslip.employee_id == own_employee(db, u).id, PayrollRun.status == "final")
    if month: q = q.filter(PayrollRun.month == month)
    return [_slip_out(db, p) for p in q.order_by(PayrollRun.month.desc())]

@router.get("/payslips/{pid}/pdf")
def payslip_pdf(pid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    p = db.get(Payslip, pid)
    if not p: raise HTTPException(404, "Not found")
    run = db.get(PayrollRun, p.run_id); e = db.get(Employee, p.employee_id)
    if u.role == "employee" and (e.user_id != u.id or run.status != "final"): raise HTTPException(403, "Not your payslip")
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4); y = 800
    c.setFont("Helvetica-Bold", 16); c.drawString(50, y, f"Payslip - {run.month}"); y -= 30
    c.setFont("Helvetica", 11)
    rows = [("Employee", f"{e.name} ({e.code})"), ("Status", run.status.upper()), ("Basic", p.basic), ("HRA (40%)", p.hra),
            ("Medical (10%)", p.medical), ("Gross", p.gross), ("Working days", p.working_days),
            ("Absent days", p.absent_days), ("Unpaid leave days", p.unpaid_days), ("Half days", p.half_days),
            ("Deductions", p.deductions), ("Income tax", p.tax), ("NET PAY", p.net)]
    for k, v in rows:
        c.drawString(50, y, str(k)); c.drawRightString(400, y, f"{v:,.2f}" if isinstance(v, float) else str(v)); y -= 22
    c.save(); buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename=payslip-{run.month}.pdf"})
