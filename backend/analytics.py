"""Single source of truth for every metric. Dashboards AND AI tools call these functions,
so AI answers always match the dashboard (Section 6, Figure 3)."""
import datetime as dt
from collections import Counter, defaultdict
from sqlalchemy import func
from models import (Setting, Holiday, Employee, Department, Designation, Attendance,
                    LeaveRequest, LeaveType, PayrollRun, Payslip)
from rules import is_working_day
from core import today

def ctx(db):
    s = db.query(Setting).first()
    weekend = {int(x) for x in (s.weekend or "").split(",") if x != ""}
    return s, weekend, {h.date for h in db.query(Holiday)}

def daterange(a, b):
    d = a
    while d <= b:
        yield d; d += dt.timedelta(1)

def rate_of(c):
    tot = sum(c.values()) - c.get("on_leave", 0)
    if tot <= 0: return None
    return round(100 * (c["present"] + c["late"] + c["missing_checkout"] + 0.5 * c["half_day"]) / tot, 1)

def pct(a, b): return round(100 * a / b, 1) if b else None

def scoped_employees(db, dept_id=None, emp_id=None):
    q = db.query(Employee)
    if dept_id: q = q.filter(Employee.department_id == dept_id)
    if emp_id: q = q.filter(Employee.id == emp_id)
    return q.all()

def leave_map(db, emp_ids, start, end, c=None):
    _, weekend, hol = c or ctx(db)
    out = defaultdict(dict)
    if not emp_ids: return out
    rows = (db.query(LeaveRequest, LeaveType).join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .filter(LeaveRequest.status == "approved", LeaveRequest.employee_id.in_(emp_ids),
                    LeaveRequest.start_date <= end, LeaveRequest.end_date >= start).all())
    for lr, lt in rows:
        for d in daterange(max(lr.start_date, start), min(lr.end_date, end)):
            if is_working_day(d, weekend, hol): out[lr.employee_id][d] = (lt.name, lt.paid)
    return out

def daily_records(db, emps, start, end):
    """Derived per-day status: present|late|half_day|missing_checkout|on_leave|absent."""
    c = ctx(db); _, weekend, hol = c; t = today(); end = min(end, t)
    if not emps or start > end: return []
    ids = [e.id for e in emps]
    att = {(a.employee_id, a.date): a for a in db.query(Attendance).filter(
        Attendance.employee_id.in_(ids), Attendance.date.between(start, end))}
    lv = leave_map(db, ids, start, end, c)
    out = []
    for e in emps:
        lo = max(start, e.join_date); hi = min(end, e.exit_date) if e.exit_date else end
        for d in daterange(lo, hi):
            if not is_working_day(d, weekend, hol): continue
            a = att.get((e.id, d))
            if a:
                st = "missing_checkout" if (a.check_out is None and d < t) else a.status
            elif d in lv[e.id]: st = "on_leave"
            elif d < t: st = "absent"
            else: continue
            out.append({"employee_id": e.id, "department_id": e.department_id, "date": d, "status": st,
                        "check_in": a.check_in if a else None, "check_out": a.check_out if a else None})
    return out

def attendance_summary(db, start, end, dept_id=None, emp_id=None):
    emps = scoped_employees(db, dept_id, emp_id)
    recs = daily_records(db, emps, start, end)
    cnt = Counter(r["status"] for r in recs)
    bd, by_day = defaultdict(Counter), defaultdict(Counter)
    for r in recs:
        bd[r["department_id"]][r["status"]] += 1; by_day[r["date"]][r["status"]] += 1
    names = {d.id: d.name for d in db.query(Department)}
    return {"period": {"from": start, "to": end}, "counts": dict(cnt), "attendance_rate": rate_of(cnt),
            "late_pct": pct(cnt["late"], cnt["present"] + cnt["late"]),
            "absenteeism_pct": pct(cnt["absent"], len(recs)),
            "by_department": [{"department": names.get(k, "Unassigned"), "attendance_rate": rate_of(v),
                               "late_pct": pct(v["late"], v["present"] + v["late"]),
                               "absenteeism_pct": pct(v["absent"], sum(v.values()))} for k, v in bd.items()],
            "trend": [{"date": d, "attendance_rate": rate_of(c)} for d, c in sorted(by_day.items())]}

def late_comers(db, start, end, dept_id=None, limit=5):
    emps = scoped_employees(db, dept_id)
    nm = {e.id: e.name for e in emps}
    c = Counter(r["employee_id"] for r in daily_records(db, emps, start, end) if r["status"] == "late")
    return [{"employee_id": i, "name": nm[i], "late_days": n} for i, n in c.most_common(limit)]

def headcount_on(db, d, dept_id=None):
    q = db.query(func.count(Employee.id)).filter(Employee.join_date <= d,
                                                 (Employee.exit_date.is_(None)) | (Employee.exit_date > d))
    if dept_id: q = q.filter(Employee.department_id == dept_id)
    return q.scalar() or 0

def headcount(db, dept_id=None):
    t = today()
    base = db.query(Employee).filter(Employee.join_date <= t, (Employee.exit_date.is_(None)) | (Employee.exit_date > t))
    if dept_id: base = base.filter(Employee.department_id == dept_id)
    by_dept = (base.outerjoin(Department, Department.id == Employee.department_id)
               .with_entities(func.coalesce(Department.name, "Unassigned"), func.count(Employee.id))
               .group_by(Department.name).all())
    return {"total": base.count(), "by_department": [{"department": n, "count": c} for n, c in by_dept]}

def attrition(db, start, end, dept_id=None):
    q = db.query(func.count(Employee.id)).filter(Employee.exit_date.between(start, end))
    if dept_id: q = q.filter(Employee.department_id == dept_id)
    exits = q.scalar() or 0
    avg = (headcount_on(db, start, dept_id) + headcount_on(db, end, dept_id)) / 2
    return {"exits": exits, "attrition_pct": pct(exits, avg) if avg else None}

def leave_summary(db, start, end, dept_id=None, emp_id=None):
    q = (db.query(LeaveType.name, LeaveRequest.status, func.coalesce(func.sum(LeaveRequest.days), 0))
         .join(LeaveRequest, LeaveRequest.leave_type_id == LeaveType.id)
         .join(Employee, Employee.id == LeaveRequest.employee_id)
         .filter(LeaveRequest.start_date.between(start, end), LeaveRequest.status.in_(("approved", "pending"))))
    if dept_id: q = q.filter(Employee.department_id == dept_id)
    if emp_id: q = q.filter(Employee.id == emp_id)
    agg = defaultdict(lambda: {"approved_days": 0, "pending_days": 0})
    for n, st, days in q.group_by(LeaveType.name, LeaveRequest.status).all():
        agg[n][f"{st}_days"] = float(days)
    scope = 1 if emp_id else (headcount(db, dept_id)["total"])
    quotas = {t.name: t.quota for t in db.query(LeaveType)}
    by_type = [{"type": n, **v, "utilization_pct": pct(v["approved_days"], quotas[n] * scope) if quotas.get(n) else None}
               for n, v in agg.items()]
    emps = scoped_employees(db, dept_id, emp_id); t = today()
    lv = leave_map(db, [e.id for e in emps], t, t)
    nm = {e.id: e.name for e in emps}
    on_leave = [{"name": nm[i], "type": v[t][0]} for i, v in lv.items() if t in v]
    return {"period": {"from": start, "to": end}, "by_type": by_type,
            "on_leave_today": {"count": len(on_leave), "employees": on_leave}}

def leave_balance(db, emp_id, year=None):
    year = year or today().year
    rng = (dt.date(year, 1, 1), dt.date(year, 12, 31))
    used = defaultdict(lambda: {"approved": 0.0, "pending": 0.0})
    for tid, st, days in (db.query(LeaveRequest.leave_type_id, LeaveRequest.status, func.sum(LeaveRequest.days))
                          .filter(LeaveRequest.employee_id == emp_id, LeaveRequest.start_date.between(*rng),
                                  LeaveRequest.status.in_(("approved", "pending"))).group_by(
                              LeaveRequest.leave_type_id, LeaveRequest.status)):
        used[tid][st] = float(days)
    return [{"leave_type_id": t.id, "type": t.name, "quota": t.quota, "used": used[t.id]["approved"],
             "pending": used[t.id]["pending"],
             "remaining": None if t.quota is None else t.quota - used[t.id]["approved"]}
            for t in db.query(LeaveType).order_by(LeaveType.id)]

def payroll_summary(db, month=None, dept_id=None):
    run = (db.query(PayrollRun).filter(PayrollRun.month == month).first() if month
           else db.query(PayrollRun).order_by(PayrollRun.month.desc()).first())
    if not run: return {"month": month, "message": "No payroll run found"}
    q = db.query(Payslip.department_id, func.sum(Payslip.gross), func.sum(Payslip.net), func.count(Payslip.id)
                 ).filter(Payslip.run_id == run.id)
    if dept_id: q = q.filter(Payslip.department_id == dept_id)
    names = {d.id: d.name for d in db.query(Department)}
    rows = q.group_by(Payslip.department_id).all()
    return {"month": run.month, "status": run.status,
            "total_gross": round(sum(r[1] or 0 for r in rows), 2), "total_net": round(sum(r[2] or 0 for r in rows), 2),
            "employees": sum(r[3] for r in rows),
            "by_department": [{"department": names.get(r[0], "Unassigned"), "gross": round(r[1] or 0, 2),
                               "net": round(r[2] or 0, 2)} for r in rows]}

def search_employees(db, q=None, dept_id=None, status=None, page=1, size=20, emp_id=None):
    qs = db.query(Employee)
    if emp_id: qs = qs.filter(Employee.id == emp_id)
    if dept_id: qs = qs.filter(Employee.department_id == dept_id)
    if status: qs = qs.filter(Employee.status == status)
    if q:
        like = f"%{q.lower()}%"
        qs = qs.filter(func.lower(Employee.name).like(like) | func.lower(Employee.email).like(like) |
                       func.lower(Employee.code).like(like))
    total = qs.count()
    rows = qs.order_by(Employee.id).offset((page - 1) * size).limit(size).all()
    dn = {d.id: d.name for d in db.query(Department)}; gn = {d.id: d.name for d in db.query(Designation)}
    items = [{"id": e.id, "code": e.code, "name": e.name, "email": e.email, "phone": e.phone, "address": e.address,
              "gender": e.gender, "department_id": e.department_id, "department": dn.get(e.department_id),
              "designation_id": e.designation_id, "designation": gn.get(e.designation_id),
              "join_date": e.join_date, "exit_date": e.exit_date, "status": e.status,
              "basic_salary": e.basic_salary} for e in rows]
    return {"total": total, "page": page, "size": size, "items": items}
