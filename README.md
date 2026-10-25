# HCM Lite - AI-assisted Human Capital Management
Saylani Hackathon (24 Oct 2026) · FastAPI + SQLAlchemy (SQLite local / PostgreSQL on Render) · JWT + bcrypt · LLM tool-calling · scikit-learn

Starts **empty**: Admin sets up the company, HR registers employees, every number comes from real activity.

## Architecture
```
Frontend (any SPA) --REST/JWT--> FastAPI routers --> rules.py (pure business rules)
                                        |            analytics.py (ONE source of metrics)
                                        |                 ^            ^
                                        |        dashboards        ai_service.py tools --> LLM API (key on server only)
                                        v
                                 SQLAlchemy -> SQLite | PostgreSQL
```
| Module | File | Highlights |
|---|---|---|
| M1 Auth & RBAC | `routers/auth.py`, `core.py` | JWT, bcrypt, `require_roles`, employee data-scope, forced password change |
| M2 Setup | `routers/org.py` | settings, departments, designations, leave types, holidays (Admin writes) |
| M3 Employees | `routers/employees.py` | register -> login, search/paging, Employee 360, CSV **dry-run** (row+field+reason), export |
| M4 Attendance | `routers/attendance.py` | server-stamped time, 409 on 2nd check-in, late/half_day/absent/missing_checkout, corrections, camera + Haversine geofence |
| M5 Leave | `routers/leave.py` | working-days only, overlap/over-balance = 422, approval w/ comment |
| M6 Payroll | `routers/payroll.py`, `rules.py` | gross = 1.5 x basic, proration, tax slab, draft -> Admin finalize (locked), payslip PDF |
| M7 Dashboards | `routers/dashboard.py`, `analytics.py` | Admin / HR / Employee KPIs, trends, date + department filters |
| M8 AI | `ai_service.py`, `routers/ai.py`, `ml_model.py` | chat with tools, summary, smart leave, policy Q&A, attrition risk |
| M9 Comms & Audit | `routers/comms.py` | announcements, notifications, immutable audit log (ORM blocks update/delete) |

## Run locally
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env            # then edit; export vars or use your process manager
uvicorn main:app --reload          # http://localhost:8000/docs
pytest -q                          # 7 tests: rules, full RBAC flow, mocked AI tool loop
```

## Create the first Admin (fresh install)
Option A - API / UI first-run screen: `GET /auth/status` -> `{"setup_done": false}` then
```bash
curl -X POST localhost:8000/auth/setup -H 'Content-Type: application/json' \
  -d '{"company_name":"Acme","email":"admin@acme.com","password":"Admin#1234"}'
```
Option B - env file: set `ADMIN_EMAIL`, `ADMIN_PASSWORD` (and `COMPANY_NAME`); created on first boot.

Then: Admin sets office hours + **office lat/lng/radius** (`PUT /org/settings`), adds a department, creates an HR login
(`POST /auth/users` -> temporary password) -> HR registers employees (`POST /employees` -> temporary password, must be changed on first sign-in).

## Set the AI key
`AI_API_KEY` is read **only** by the backend. Any OpenAI-compatible provider with tool calling works
(default: Groq free tier, `llama-3.3-70b-versatile`; change `AI_BASE_URL` / `AI_MODEL` for OpenAI, Gemini-compat, OpenRouter).
Without a key the app still works and AI endpoints return a friendly fallback.
Guardrails: 15 s timeout, 20 req/user/hour (429), every call in `ai_logs`, tools run with the caller's scope, AI never saves data
(smart leave returns a pre-filled form; user confirms via `POST /leave`), answers labelled `ai_generated: true`.

## Deploy on Render
1. Push repo to GitHub. 2. Render -> **New -> Blueprint** -> select repo (uses `render.yaml`: Docker web service + free PostgreSQL).
3. In the service's Environment tab set `AI_API_KEY`, `CORS_ORIGINS` (frontend URL), optionally `ADMIN_EMAIL`/`ADMIN_PASSWORD`.
4. Health check: `/health`; Swagger: `/docs`. (Render free web services sleep after idle - open the URL once before the demo.)

## Key endpoints
`/auth/*` · `/org/*` · `/employees` · `/attendance` · `/leave` · `/payroll/runs` · `/payroll/payslips/{id}/pdf` ·
`/dashboard` · `/announcements` · `/notifications` · `/audit-log` · `/ai/chat` · `/ai/summarize` · `/ai/smart-leave` · `/ai/attrition-risk`.
Frontend fetch client: `frontend/api.js`.

## Bonus implemented
Camera attendance API (one face + GPS accuracy <= 100 m + server-side Haversine <= radius; no photo stored) ·
attrition risk score (`ml_model.py`: logistic regression on 90-day lateness/absence/leave/tenure/salary-ratio features; trains on real data once
>=30 employees with >=5 exits exist, otherwise a synthetic *prior* initialises it - reported as `model_source`) ·
AI replies in Roman Urdu.

## Done / not done
**Done:** all 25 checklist functionalities server-side, RBAC on every route, tests, Docker/Render config.
**Not done / limits:** no web UI shipped here (bring your frontend; `frontend/api.js` wires it) · browser face detection runs in the frontend
(server trusts `face_count` + validates location; no face-descriptor matching/liveness) · no email delivery of temporary passwords (shown once in API response) ·
analytics aggregate in Python for attendance (derived statuses), SQL aggregation for headcount/leave/payroll · late-penalty rule and PWA not implemented.
