"""Employee Attrition Risk Score (scikit-learn).
Features come from the live DB via analytics.daily_records (last 90 days before ref date).
Training source: REAL (employees with exit_date = positives) once the company has >=30 employees with >=5 exits and
>=5 stayers; otherwise a synthetic *prior* is used ONLY to initialise the model (never stored or shown as app data).
Run:  python ml_model.py"""
import datetime as dt, time
import numpy as np
import analytics as A
from core import today

FEATURES = ["tenure_months", "late_rate", "absent_rate", "half_day_rate", "leave_days_90d", "salary_ratio"]
_cache = {"model": None, "source": None, "at": 0}

def features(db, e, ref, dept_mean):
    recs = A.daily_records(db, [e], ref - dt.timedelta(90), ref)
    c = {}
    for r in recs: c[r["status"]] = c.get(r["status"], 0) + 1
    n = len(recs) or 1; worked = (c.get("present", 0) + c.get("late", 0)) or 1
    return [max((ref - e.join_date).days / 30.4, 0), c.get("late", 0) / worked, c.get("absent", 0) / n,
            c.get("half_day", 0) / n, c.get("on_leave", 0), (e.basic_salary / dept_mean) if dept_mean else 1.0]

def _synthetic(n=800, seed=7):
    g = np.random.default_rng(seed)
    ten = g.gamma(2.0, 14, n); late = g.beta(1.5, 9, n); ab = g.beta(1.2, 18, n); hd = g.beta(1.2, 15, n)
    lv = g.poisson(4, n); sal = np.clip(g.normal(1, .25, n), .4, 2)
    z = -2.4 + 3.5 * late + 9 * ab + 3 * hd - .025 * ten + .04 * lv - 1.6 * (sal - 1) + (ten < 6) * .8
    y = (g.random(n) < 1 / (1 + np.exp(-z))).astype(int)
    return np.column_stack([ten, late, ab, hd, lv, sal]), y

def _dept_means(db):
    from collections import defaultdict
    m = defaultdict(list)
    for e in A.scoped_employees(db):
        if e.basic_salary: m[e.department_id].append(e.basic_salary)
    return {k: sum(v) / len(v) for k, v in m.items()}

def train(db):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    emps = A.scoped_employees(db); dm = _dept_means(db)
    exited = [e for e in emps if e.exit_date]; stay = [e for e in emps if not e.exit_date]
    if len(emps) >= 30 and len(exited) >= 5 and len(stay) >= 5:
        X = [features(db, e, e.exit_date or today(), dm.get(e.department_id)) for e in emps]
        y = [1 if e.exit_date else 0 for e in emps]; src = "real"
    else:
        X, y = _synthetic(); src = "synthetic_prior"
    model = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=500)).fit(X, y)
    _cache.update(model=model, source=src, at=time.time())
    return model

def score_employees(db, dept_id=None):
    if not _cache["model"] or time.time() - _cache["at"] > 600: train(db)
    model = _cache["model"]; dm = _dept_means(db); t = today()
    emps = [e for e in A.scoped_employees(db, dept_id) if not e.exit_date]
    if not emps: return {"model_source": _cache["source"], "employees": []}
    X = np.array([features(db, e, t, dm.get(e.department_id)) for e in emps])
    p = model.predict_proba(X)[:, 1]
    scaler, lr = model.steps[0][1], model.steps[1][1]
    contrib = scaler.transform(X) * lr.coef_[0]
    out = []
    for e, pr, c in zip(emps, p, contrib):
        top = [FEATURES[i] for i in np.argsort(-c)[:2] if c[i] > 0]
        out.append({"employee_id": e.id, "name": e.name, "risk_score": round(float(pr) * 100, 1),
                    "band": "high" if pr >= .6 else "medium" if pr >= .3 else "low", "top_factors": top})
    return {"model_source": _cache["source"], "employees": sorted(out, key=lambda r: -r["risk_score"])}

if __name__ == "__main__":
    from db import SessionLocal
    with SessionLocal() as s:
        train(s); print("trained on:", _cache["source"])
        for r in score_employees(s)["employees"][:10]: print(r)
