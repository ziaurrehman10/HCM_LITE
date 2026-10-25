import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import Base, engine, SessionLocal
import models  # noqa: F401  (register tables)
from models import User
from routers import auth, org, employees, attendance, leave, payroll, dashboard, comms, ai

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    # First run via env file (brief, Section 2): ADMIN_EMAIL / ADMIN_PASSWORD [/ COMPANY_NAME]
    em, pw = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    if em and pw:
        with SessionLocal() as db:
            if not db.query(User).filter(User.role == "admin").first():
                auth.bootstrap(db, os.getenv("COMPANY_NAME", "My Company"), "Admin", em, pw)
    yield

app = FastAPI(title="HCM Lite API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth, org, employees, attendance, leave, payroll, dashboard, comms, ai):
    app.include_router(r.router)

@app.get("/health", tags=["meta"])
def health(): return {"status": "ok"}
