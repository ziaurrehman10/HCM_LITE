import datetime as dt
from fastapi.testclient import TestClient
from main import app
import core

def H(tok): return {"Authorization": f"Bearer {tok}"}

def test_full_flow():
    with TestClient(app) as c:
        assert c.get("/auth/status").json()["setup_done"] is False
        r = c.post("/auth/setup", json={"company_name": "Acme", "email": "admin@acme.com", "password": "Admin#1234"}); assert r.status_code == 201
        A = r.json()["token"]
        assert c.post("/auth/setup", json={"company_name": "Other Co", "email": "a@b.com", "password": "12345678"}).status_code == 409
        c.put("/org/settings", headers=H(A), json={"office_lat": 24.86, "office_lng": 67.0, "radius_m": 150})
        dep = c.post("/org/departments", headers=H(A), json={"name": "Engineering"}).json()["id"]
        hr = c.post("/auth/users", headers=H(A), json={"email": "hr@acme.com", "name": "Hina HR"}).json()
        assert c.post("/auth/login", json={"email": "hr@acme.com", "password": hr["temporary_password"]}).status_code == 200
        tok = c.post("/auth/login", json={"email": "hr@acme.com", "password": hr["temporary_password"]}).json()["token"]
        assert c.get("/employees", headers=H(tok)).status_code == 403            # must change password first
        c.post("/auth/change-password", headers=H(tok), json={"old_password": hr["temporary_password"], "new_password": "Hr#12345"})
        HR = c.post("/auth/login", json={"email": "hr@acme.com", "password": "Hr#12345"}).json()["token"]
        assert c.post("/employees", headers=H(A), json={}).status_code == 403   # admin cannot register
        mk = lambda n, e: c.post("/employees", headers=H(HR), json={"name": n, "email": e, "department_id": dep,
                                  "join_date": "2026-01-05", "basic_salary": 100000}).json()
        e1, e2 = mk("Ali Khan", "ali@acme.com"), mk("Sara Noor", "sara@acme.com")
        def emp_login(e):
            t = c.post("/auth/login", json={"email": e["email"], "password": e["temporary_password"]}).json()["token"]
            c.post("/auth/change-password", headers=H(t), json={"old_password": e["temporary_password"], "new_password": "Emp#12345"})
            return c.post("/auth/login", json={"email": e["email"], "password": "Emp#12345"}).json()["token"]
        E1, E2 = emp_login(e1), emp_login(e2)
        # scope guard
        assert c.get(f"/employees/{e2['id']}", headers=H(E1)).status_code == 403
        assert [x["id"] for x in c.get("/employees", headers=H(E1)).json()["items"]] == [e1["id"]]
        assert c.get("/audit-log", headers=H(HR)).status_code == 403
        # attendance
        assert c.post("/attendance/check-in", headers=H(E1)).status_code == 201
        assert c.post("/attendance/check-in", headers=H(E1)).status_code == 409
        bad = c.post("/attendance/check-in", headers=H(E2), json={"source": "camera", "face_count": 1, "lat": 25.5, "lng": 67.0, "accuracy": 10})
        assert bad.status_code == 403
        ok = c.post("/attendance/check-in", headers=H(E2), json={"source": "camera", "face_count": 1, "lat": 24.8601, "lng": 67.0, "accuracy": 10})
        assert ok.status_code == 201 or ok.status_code == 409
        assert c.post("/attendance/check-out", headers=H(E1)).status_code == 200
        # leave
        lt = {t["name"]: t["id"] for t in c.get("/org/leave-types", headers=H(E1)).json()}
        nxt = dt.date.today() + dt.timedelta(days=30)
        while nxt.weekday() != 0: nxt += dt.timedelta(1)
        body = {"leave_type_id": lt["Casual"], "start_date": str(nxt), "end_date": str(nxt + dt.timedelta(1)), "reason": "wedding"}
        r = c.post("/leave", headers=H(E1), json=body); assert r.status_code == 201 and r.json()["days"] == 2
        assert c.post("/leave", headers=H(E1), json=body).status_code == 422       # overlap
        big = {**body, "start_date": str(nxt + dt.timedelta(7)), "end_date": str(nxt + dt.timedelta(40))}
        assert c.post("/leave", headers=H(E1), json=big).status_code == 422        # over balance
        assert c.post(f"/leave/{r.json()['id']}/decide", headers=H(E1), json={"approve": True}).status_code == 403
        assert c.post(f"/leave/{r.json()['id']}/decide", headers=H(HR), json={"approve": True, "comment": "ok"}).status_code == 200
        bal = {b["type"]: b for b in c.get("/leave/balance", headers=H(E1)).json()}
        assert bal["Casual"]["remaining"] == 8
        # payroll
        month = dt.date.today().strftime("%Y-%m")
        run = c.post("/payroll/runs", headers=H(HR), json={"month": month}); assert run.status_code == 201
        assert c.post(f"/payroll/runs/{run.json()['id']}/finalize", headers=H(HR)).status_code == 403
        assert c.post(f"/payroll/runs/{run.json()['id']}/finalize", headers=H(A)).status_code == 200
        assert c.post("/payroll/runs", headers=H(HR), json={"month": month}).status_code == 409   # locked
        slips = c.get("/payroll/payslips", headers=H(E1)).json(); assert len(slips) == 1 and slips[0]["gross"] == 150000
        pdf = c.get(f"/payroll/payslips/{slips[0]['id']}/pdf", headers=H(E1)); assert pdf.content[:4] == b"%PDF"
        assert c.get(f"/payroll/salary/{e2['id']}", headers=H(E1)).status_code == 403
        # dashboards, notifications, audit
        assert c.get("/dashboard", headers=H(HR)).json()["kpis"]["headcount"] == 2
        assert "attrition_ytd_pct" in c.get("/dashboard", headers=H(A)).json()["kpis"]
        assert c.get("/dashboard", headers=H(E1)).json()["role"] == "employee"
        assert any("Payslip" in n["title"] for n in c.get("/notifications", headers=H(E1)).json())
        log = c.get("/audit-log", headers=H(A)).json(); assert log["total"] > 5
        # CSV dry run
        csv = b"code,name,email,department,join_date,basic_salary\nE9,Bad,notanemail,Nope,2026-13-01,-5\n"
        dry = c.post("/employees/import?dry_run=true", headers=H(HR), files={"file": ("e.csv", csv)}).json()
        assert {x["field"] for x in dry["errors"]} == {"email", "department", "join_date", "basic_salary"}
        # AI without key degrades gracefully; ML scores
        assert c.post("/ai/chat", headers=H(E1), json={"message": "hi"}).json()["error"] is True
        assert c.post("/ai/smart-leave", headers=H(E1), json={"text": "off next Monday"}).json()["fallback"] is True
        risk = c.get("/ai/attrition-risk", headers=H(HR)).json(); assert len(risk["employees"]) == 2
        # AI tool scope (no LLM needed)
        import ai_service as S
        from db import SessionLocal
        from models import User
        with SessionLocal() as db:
            emp_u = db.query(User).filter_by(email="ali@acme.com").first(); hr_u = db.query(User).filter_by(email="hr@acme.com").first()
            assert "error" in S.run_tool(db, emp_u, "search_employees", {"query": "Sara"})
            assert S.run_tool(db, hr_u, "get_headcount", {})["total"] == 2
            assert S.run_tool(db, emp_u, "get_my_leave_balance", {"leave_type": "Sick"})["balances"][0]["remaining"] == 8
