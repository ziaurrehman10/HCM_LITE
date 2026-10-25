import json, datetime as dt
import ai_service as S
from main import app
from fastapi.testclient import TestClient
from db import SessionLocal
from models import User

def test_tool_loop_and_smart_leave(monkeypatch):
    with TestClient(app) as c:
        c.post("/auth/setup", json={"company_name": "AI Co", "email": "ad@ai.com", "password": "Admin#1234"})
        with SessionLocal() as db:
            admin = db.query(User).filter_by(role="admin").first()
            seq = iter([{"content": None, "tool_calls": [{"id": "1", "function": {"name": "get_headcount", "arguments": "{}"}}]},
                        {"content": "There are 0 employees."}])
            monkeypatch.setattr(S, "_post", lambda *a, **k: next(seq))
            ans, calls = S.chat(db, admin, "How many employees?")
            assert ans == "There are 0 employees." and calls[0]["tool"] == "get_headcount"
            monkeypatch.setattr(S, "_post", lambda *a, **k: {"content": '```json\n{"type":"casual","start":"2026-11-02","end":"2026-11-03","reason":"wedding"}\n```'})
            form = S.smart_leave(db, "Need Monday and Tuesday off for a wedding")
            assert form["type"] == "Casual" and form["days_preview"] == 2
            monkeypatch.setattr(S, "_post", lambda *a, **k: {"content": "garbage"})
            try: S.smart_leave(db, "x y z"); assert False
            except S.AIError: pass
