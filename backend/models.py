import datetime as dt
from sqlalchemy import (Column, Integer, String, Float, Boolean, Date, DateTime, Text,
                        ForeignKey, UniqueConstraint, event)
from sqlalchemy.orm import relationship
from db import Base

def _now(): return dt.datetime.utcnow()

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    company_name = Column(String, nullable=False)
    office_start = Column(String, default="09:00")
    grace_min = Column(Integer, default=15)
    half_day_hours = Column(Float, default=5.0)
    weekend = Column(String, default="5,6")          # Mon=0 ... Sun=6
    office_lat = Column(Float); office_lng = Column(Float)
    radius_m = Column(Float, default=150.0)
    max_accuracy_m = Column(Float, default=100.0)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, default="")
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)             # admin | hr | employee
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True); name = Column(String, unique=True, nullable=False)

class Designation(Base):
    __tablename__ = "designations"
    id = Column(Integer, primary_key=True); name = Column(String, unique=True, nullable=False)

class LeaveType(Base):
    __tablename__ = "leave_types"
    id = Column(Integer, primary_key=True); name = Column(String, unique=True, nullable=False)
    quota = Column(Integer, nullable=True)            # None = unlimited
    paid = Column(Boolean, default=True)

class Holiday(Base):
    __tablename__ = "holidays"
    id = Column(Integer, primary_key=True); date = Column(Date, unique=True, nullable=False)
    name = Column(String, nullable=False)

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String); address = Column(String); gender = Column(String)
    department_id = Column(Integer, ForeignKey("departments.id"))
    designation_id = Column(Integer, ForeignKey("designations.id"))
    join_date = Column(Date, nullable=False); exit_date = Column(Date)
    status = Column(String, default="active")         # active | probation | exited
    basic_salary = Column(Float, default=0)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    department = relationship("Department"); designation = relationship("Designation")

class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    check_in = Column(DateTime); check_out = Column(DateTime)
    status = Column(String, default="present")        # present | late | half_day
    source = Column(String, default="web")            # web | camera | correction
    lat = Column(Float); lng = Column(Float); distance_m = Column(Float)
    __table_args__ = (UniqueConstraint("employee_id", "date"),)

class Correction(Base):
    __tablename__ = "attendance_corrections"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False)
    check_in = Column(String); check_out = Column(String)   # "HH:MM"
    reason = Column(Text, nullable=False)
    status = Column(String, default="pending"); comment = Column(Text)
    decided_by = Column(Integer, ForeignKey("users.id")); created_at = Column(DateTime, default=_now)

class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    start_date = Column(Date, nullable=False); end_date = Column(Date, nullable=False)
    days = Column(Float, nullable=False); reason = Column(Text)
    status = Column(String, default="pending")        # pending|approved|rejected|cancelled
    comment = Column(Text); decided_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_now)

class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    id = Column(Integer, primary_key=True)
    month = Column(String, unique=True, nullable=False)     # YYYY-MM
    status = Column(String, default="draft")                # draft | final
    created_by = Column(Integer, ForeignKey("users.id"))
    finalized_by = Column(Integer, ForeignKey("users.id")); finalized_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)

class Payslip(Base):
    __tablename__ = "payslips"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"))
    basic = Column(Float); hra = Column(Float); medical = Column(Float); gross = Column(Float)
    working_days = Column(Integer); active_days = Column(Integer)
    absent_days = Column(Float); unpaid_days = Column(Float); half_days = Column(Float)
    deductions = Column(Float); tax = Column(Float); net = Column(Float)
    __table_args__ = (UniqueConstraint("run_id", "employee_id"),)

class Announcement(Base):
    __tablename__ = "announcements"
    id = Column(Integer, primary_key=True); title = Column(String, nullable=False)
    body = Column(Text, nullable=False); author_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=_now)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String); body = Column(Text); is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer); actor_email = Column(String)
    action = Column(String, index=True); entity = Column(String, index=True); entity_id = Column(String)
    before = Column(Text); after = Column(Text); created_at = Column(DateTime, default=_now)

@event.listens_for(AuditLog, "before_update")
def _audit_no_update(*_): raise PermissionError("Audit log is immutable")
@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(*_): raise PermissionError("Audit log is immutable")

class AiLog(Base):
    __tablename__ = "ai_logs"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, index=True)
    kind = Column(String); prompt = Column(Text); tool_calls = Column(Text)
    response = Column(Text); status = Column(String); latency_ms = Column(Integer)
    created_at = Column(DateTime, default=_now, index=True)
