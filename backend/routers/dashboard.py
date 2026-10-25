import datetime as dt
from collections import Counter
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
import analytics as A
from db import get_db
from models import (Employee, Department, Announcement, AuditLog, Correction, LeaveRequest, Payslip, PayrollRun)
from core import current_user, own_employee, today, to_dict

router = APIRouter(prefix="/dashboard", tags=["M7 Dashboards"])

def _announcements(db):
    return [to_dict(a) for a in db.query(Announcement).order_by(Announcement.id.desc()).limit(5)]

def build(db, user, start, end, dept_id=None):
    t = today(); ann = _announcements(db)
    if user.role == "employee":
        e = own_employee(db, user); mon = t - dt.timedelta(t.weekday())
        recs = A.daily_records(db, [e], mon, t); hrs = sum((r["check_out"] - r["check_in"]).total_seconds() / 3600
                                                       for r in recs if r["check_in"] and r["check_out"])
        month = A.attendance_summary(db, t.replace(day=1), t, emp_id=e.id)
        last = (db.query(Payslip, PayrollRun.month).join(PayrollRun).filter(Payslip.employee_id == e.id, PayrollRun.status == "final")
                .order_by(PayrollRun.month.desc()).first())
        return {"role": "employee", "hours_this_week": round(hrs, 1), "weekly_target": 40,
                "late_count_month": month["counts"].get("late", 0), "leave_balance": A.leave_balance(db, e.id),
                "last_payslip": (to_dict(last[0]) | {"month": last[1]}) if last else None, "announcements": ann}
    att = A.attendance_summary(db, start, end, dept_id)
    lv = A.leave_summary(db, start, end, dept_id)
    d = {"role": user.role, "filters": {"from": start, "to": end, "department_id": dept_id}, "announcements": ann,
         "attendance": att, "leave": lv, "top_late_comers": A.late_comers(db, start, end, dept_id, 5)}
    if user.role == "hr":
        today_c = A.attendance_summary(db, t, t, dept_id)["counts"]
        d["kpis"] = {"headcount": A.headcount(db, dept_id)["total"], "present_today": today_c.get("present", 0) + today_c.get("late", 0),
                     "on_leave_today": lv["on_leave_today"]["count"], "late_today": today_c.get("late", 0),
                     "pending_approvals": db.query(LeaveRequest).filter_by(status="pending").count() +
                                          db.query(Correction).filter_by(status="pending").count()}
        return d
    hc = A.headcount(db, dept_id); ytd = A.attrition(db, dt.date(t.year, 1, 1), t, dept_id)
    pay = A.payroll_summary(db, None, dept_id)
    act = db.query(Employee).filter(Employee.exit_date.is_(None), Employee.basic_salary > 0)
    if dept_id: act = act.filter(Employee.department_id == dept_id)
    avg = act.with_entities(func.avg(Employee.basic_salary)).scalar()
    gender = (act.with_entities(func.coalesce(Employee.gender, "unspecified"), func.count(Employee.id))
              .group_by(Employee.gender).all())
    tenure = Counter("<1y" if (t - e.join_date).days < 365 else "1-3y" if (t - e.join_date).days < 1095 else "3y+"
                     for e in act.all())
    d["kpis"] = {"headcount": hc["total"], "attrition_ytd_pct": ytd["attrition_pct"], "exits_ytd": ytd["exits"],
                 "payroll_cost_gross": pay.get("total_gross"), "payroll_cost_net": pay.get("total_net"),
                 "payroll_month": pay.get("month"), "avg_salary_gross": round(avg * 1.5, 2) if avg else None}
    d |= {"headcount_by_department": hc["by_department"], "payroll_cost_by_department": pay.get("by_department", []),
          "gender": [{"gender": g, "count": c} for g, c in gender], "tenure": dict(tenure),
          "audit_feed": [to_dict(a, ("before", "after")) for a in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(10)]}
    return d

@router.get("")
def dashboard(date_from: dt.date | None = None, date_to: dt.date | None = None, department_id: int | None = None,
              db: Session = Depends(get_db), u=Depends(current_user)):
    t = today()
    return build(db, u, date_from or t.replace(day=1), date_to or t, department_id)
