import datetime as dt, re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from db import get_db
from models import Setting, Department, Designation, LeaveType, Holiday
from core import current_user, require_roles, audit, to_dict

router = APIRouter(prefix="/org", tags=["M2 Organization"])

def settings_out(s):
    d = to_dict(s); d["weekend"] = [int(x) for x in s.weekend.split(",") if x != ""]; return d

class SettingsIn(BaseModel):
    company_name: str | None = None
    office_start: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    grace_min: int | None = Field(None, ge=0, le=120)
    half_day_hours: float | None = Field(None, gt=0, le=12)
    weekend: list[int] | None = None
    office_lat: float | None = Field(None, ge=-90, le=90)
    office_lng: float | None = Field(None, ge=-180, le=180)
    radius_m: float | None = Field(None, gt=0)
    max_accuracy_m: float | None = Field(None, gt=0)

@router.get("/settings")
def get_settings(db: Session = Depends(get_db), u=Depends(current_user)):
    return settings_out(db.query(Setting).first())

@router.put("/settings")
def put_settings(b: SettingsIn, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
    s = db.query(Setting).first(); before = settings_out(s)
    for k, v in b.model_dump(exclude_unset=True).items():
        if k == "weekend":
            if any(x < 0 or x > 6 for x in v): raise HTTPException(422, "weekend values must be 0-6")
            v = ",".join(map(str, v))
        setattr(s, k, v)
    audit(db, a, "update", "settings", s.id, before, settings_out(s)); db.commit()
    return settings_out(s)      # applies to new records only (history is not recomputed in storage)

class NameIn(BaseModel): name: str = Field(min_length=2)
class LeaveTypeIn(BaseModel): name: str = Field(min_length=2); quota: int | None = Field(None, ge=0); paid: bool = True
class HolidayIn(BaseModel): date: dt.date; name: str = Field(min_length=2)

def crud(path, Model, Schema):
    @router.get(f"/{path}", name=f"list_{path}")
    def lst(db: Session = Depends(get_db), u=Depends(current_user)):
        return [to_dict(o) for o in db.query(Model).order_by(Model.id)]

    @router.post(f"/{path}", status_code=201, name=f"create_{path}")
    def create(b: Schema, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
        o = Model(**b.model_dump()); db.add(o)
        try: db.flush()
        except IntegrityError: db.rollback(); raise HTTPException(409, "Already exists")
        audit(db, a, "create", path, o.id, None, to_dict(o)); db.commit(); return to_dict(o)

    @router.put(f"/{path}/{{oid}}", name=f"update_{path}")
    def update(oid: int, b: Schema, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
        o = db.get(Model, oid)
        if not o: raise HTTPException(404, "Not found")
        before = to_dict(o)
        for k, v in b.model_dump().items(): setattr(o, k, v)
        try: db.flush()
        except IntegrityError: db.rollback(); raise HTTPException(409, "Already exists")
        audit(db, a, "update", path, oid, before, to_dict(o)); db.commit(); return to_dict(o)

    @router.delete(f"/{path}/{{oid}}", name=f"delete_{path}")
    def delete(oid: int, db: Session = Depends(get_db), a=Depends(require_roles("admin"))):
        o = db.get(Model, oid)
        if not o: raise HTTPException(404, "Not found")
        before = to_dict(o)
        try: db.delete(o); db.flush()
        except IntegrityError: db.rollback(); raise HTTPException(409, "In use; cannot delete")
        audit(db, a, "delete", path, oid, before, None); db.commit(); return {"ok": True}

crud("departments", Department, NameIn)
crud("designations", Designation, NameIn)
crud("leave-types", LeaveType, LeaveTypeIn)
crud("holidays", Holiday, HolidayIn)
