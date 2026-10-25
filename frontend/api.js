// Drop-in API client for the Admin / HR / Employee portals (any framework). Set VITE_API_URL / REACT_APP_API_URL.
const BASE = (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL) || "http://localhost:8000";
let token = sessionStorage.getItem("hcm_token");

async function api(path, { method = "GET", body, form } = {}) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(BASE + path, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) });
  if (res.status === 401) { sessionStorage.removeItem("hcm_token"); token = null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw Object.assign(new Error(typeof err.detail === "string" ? err.detail : "Request failed"), { status: res.status, detail: err.detail });
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.blob();
}

// ---- Auth (M1) ----
export const setupStatus = () => api("/auth/status");
export const firstRunSetup = (company_name, email, password) =>
  api("/auth/setup", { method: "POST", body: { company_name, email, password } }).then(save);
export const login = (email, password) => api("/auth/login", { method: "POST", body: { email, password } }).then(save);
export const changePassword = (old_password, new_password) => api("/auth/change-password", { method: "POST", body: { old_password, new_password } });
export const logout = () => { sessionStorage.removeItem("hcm_token"); token = null; };
function save(r) { token = r.token; sessionStorage.setItem("hcm_token", r.token); return r.user; } // user.role -> route to /admin | /hr | /employee

// ---- Admin ----
export const updateSettings = (s) => api("/org/settings", { method: "PUT", body: s });         // office lat/lng/radius, hours
export const addDepartment = (name) => api("/org/departments", { method: "POST", body: { name } });
export const createHR = (email, name) => api("/auth/users", { method: "POST", body: { email, name } }); // returns temporary_password
export const finalizePayroll = (runId) => api(`/payroll/runs/${runId}/finalize`, { method: "POST" });
export const auditLog = (q = "") => api(`/audit-log${q}`);

// ---- HR ----
export const registerEmployee = (e) => api("/employees", { method: "POST", body: e });
export const searchEmployees = (q = "", page = 1) => api(`/employees?q=${encodeURIComponent(q)}&page=${page}`);
export const csvImport = (file, dryRun = true) => { const f = new FormData(); f.append("file", file); return api(`/employees/import?dry_run=${dryRun}`, { method: "POST", form: f }); };
export const decideLeave = (id, approve, comment) => api(`/leave/${id}/decide`, { method: "POST", body: { approve, comment } });
export const createPayrollDraft = (month) => api("/payroll/runs", { method: "POST", body: { month } }); // "2026-10"
export const attritionRisk = () => api("/ai/attrition-risk");

// ---- Employee ----
export const checkIn = (extra = {}) => api("/attendance/check-in", { method: "POST", body: extra });  // camera: {source:"camera",face_count:1,lat,lng,accuracy}
export const checkOut = () => api("/attendance/check-out", { method: "POST" });
export const applyLeave = (f) => api("/leave", { method: "POST", body: f });
export const leaveBalance = () => api("/leave/balance");
export const downloadPayslip = async (id) => {
  const blob = await api(`/payroll/payslips/${id}/pdf`);
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `payslip-${id}.pdf` }); a.click();
};

// ---- Shared ----
export const dashboard = (from, to, dept) => api(`/dashboard?${new URLSearchParams({ ...(from && { date_from: from }), ...(to && { date_to: to }), ...(dept && { department_id: dept }) })}`);
export const notifications = () => api("/notifications");
export const announcements = () => api("/announcements");

// ---- AI (M8) ----
export const aiChat = (message, history = []) => api("/ai/chat", { method: "POST", body: { message, history } });
export const aiSummarize = (filters = {}) => api("/ai/summarize", { method: "POST", body: filters });
export const smartLeave = (text) => api("/ai/smart-leave", { method: "POST", body: { text } }).then(r => r.form /* null => show manual form */);

// Camera check-in (bonus): face-api / MediaPipe gives face_count; browser gives GPS
export const cameraCheckIn = (faceCount) => new Promise((ok, no) =>
  navigator.geolocation.getCurrentPosition(
    p => checkIn({ source: "camera", face_count: faceCount, lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }).then(ok, no), no,
    { enableHighAccuracy: true, timeout: 10000 }));
