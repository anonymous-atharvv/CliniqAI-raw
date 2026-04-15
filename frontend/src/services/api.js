/**
 * CliniQAI API Service
 * Typed client for all backend endpoints.
 * Handles JWT auth, auto token-refresh, and error normalisation.
 *
 * BASE_URL reads from VITE_API_BASE_URL env var:
 *   - Local dev:  http://localhost:8000
 *   - Production: https://your-app.up.railway.app
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

// ── Token storage ─────────────────────────────────────────────────────────────
let _access  = localStorage.getItem("cq_access")  || "";
let _refresh = localStorage.getItem("cq_refresh") || "";

function setTokens(access, refresh) {
  _access = access; _refresh = refresh;
  localStorage.setItem("cq_access",  access);
  if (refresh) localStorage.setItem("cq_refresh", refresh);
}
function clearTokens() {
  _access = ""; _refresh = "";
  localStorage.removeItem("cq_access");
  localStorage.removeItem("cq_refresh");
}

// ── Core fetch ────────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(_access ? { Authorization: `Bearer ${_access}` } : {}),
    ...options.headers,
  };

  let res = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  // Auto-refresh expired token
  if (res.status === 401 && _refresh) {
    const r = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: _refresh }),
    });
    if (r.ok) {
      const d = await r.json();
      setTokens(d.access_token, _refresh);
      headers.Authorization = `Bearer ${d.access_token}`;
      res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
    } else {
      clearTokens();
      window.location.href = "/login";
      throw new Error("Session expired");
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    const e = new Error(err.error || "API error");
    e.status = res.status;
    e.data   = err;
    throw e;
  }
  return res.status === 204 ? null : res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const auth = {
  login: async (username, password, hospitalId = "demo_hospital_001") => {
    const d = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, hospital_id: hospitalId }),
    });
    setTokens(d.access_token, d.refresh_token);
    return d;
  },
  logout: async () => {
    await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
    clearTokens();
  },
  isAuthenticated: () => Boolean(_access),
  getToken: () => _access,
};

// ── Patients ──────────────────────────────────────────────────────────────────
export const patients = {
  list:           (p = {}) => apiFetch(`/api/v1/patients?${new URLSearchParams(p)}`),
  get:            (id)     => apiFetch(`/api/v1/patients/${id}`),
  getIntelligence:(id)     => apiFetch(`/api/v1/patients/${id}/intelligence`),
  getVitals:      (id, h=6)=> apiFetch(`/api/v1/patients/${id}/vitals?hours=${h}`),
  getMedications: (id)     => apiFetch(`/api/v1/patients/${id}/medications`),
  getTimeline:    (id, h=24)=> apiFetch(`/api/v1/patients/${id}/timeline?hours=${h}`),
};

// ── Vitals ────────────────────────────────────────────────────────────────────
export const vitals = {
  ingest:       (r)        => apiFetch("/api/v1/vitals/ingest", { method:"POST", body:JSON.stringify(r) }),
  ingestBatch:  (rs)       => apiFetch("/api/v1/vitals/ingest/batch", { method:"POST", body:JSON.stringify({ readings: rs }) }),
  getTrend:     (id, p, h=6) => apiFetch(`/api/v1/vitals/${id}/trend?parameter=${p}&hours=${h}`),
  getAIPrediction:(id)     => apiFetch(`/api/v1/vitals/${id}/ai-prediction`),
  getWardSnapshot:(ward)   => apiFetch(`/api/v1/vitals/icu/${ward}/snapshot`),
};

// ── Inference ─────────────────────────────────────────────────────────────────
export const inference = {
  run:           (payload) => apiFetch("/api/v1/inference/patient", { method:"POST", body:JSON.stringify(payload) }),
  getHistory:    (id, h=24)=> apiFetch(`/api/v1/inference/patient/${id}/history?hours=${h}`),
  get:           (id)      => apiFetch(`/api/v1/inference/${id}`),
  submitFeedback:(id, sig, ok, reason) =>
    apiFetch(`/api/v1/inference/${id}/feedback?signal=${sig}&is_helpful=${ok}${reason ? `&reason=${encodeURIComponent(reason)}` : ""}`, { method:"POST" }),
};

// ── Agents ────────────────────────────────────────────────────────────────────
export const agents = {
  getAllStatus:          ()      => apiFetch("/api/v1/agents/status"),
  getStatus:            (id)    => apiFetch(`/api/v1/agents/status/${id}`),
  getPatientSession:    (id)    => apiFetch(`/api/v1/agents/sessions/${id}`),
  triggerPipeline:      (id, r) => apiFetch(`/api/v1/agents/sessions/${id}/trigger?reason=${encodeURIComponent(r)}`, { method:"POST" }),
  getActiveEscalations: (ward)  => apiFetch(`/api/v1/agents/escalations${ward ? `?ward_code=${ward}` : ""}`),
  acknowledgeEscalation:(id, n) => apiFetch(`/api/v1/agents/escalations/${id}/acknowledge`, { method:"POST", body:JSON.stringify({ notes: n }) }),
  getMetrics:           (h=24)  => apiFetch(`/api/v1/agents/metrics?hours=${h}`),
};

// ── Admin ─────────────────────────────────────────────────────────────────────
export const admin = {
  getCFODashboard:       ()     => apiFetch("/api/v1/admin/dashboard/cfo"),
  getCOODashboard:       ()     => apiFetch("/api/v1/admin/dashboard/coo"),
  getBedSnapshot:        ()     => apiFetch("/api/v1/admin/beds/snapshot"),
  getDischargePredictions:(h=24)=> apiFetch(`/api/v1/admin/beds/predictions/discharges?hours=${h}`),
  getReadmissionRiskList:(t=.2) => apiFetch(`/api/v1/admin/readmissions/risk-list?risk_threshold=${t}`),
  getCMSReport:          (m)    => apiFetch(`/api/v1/admin/readmissions/cms-report?month=${m}`),
  getQualityMeasures:    ()     => apiFetch("/api/v1/admin/quality/core-measures"),
  getMonthlyReport:      (m)    => apiFetch(`/api/v1/admin/financial/monthly-report?month=${m}`),
  getLOSAnalysis:        ()     => apiFetch("/api/v1/admin/financial/los-analysis"),
  getModelRegistry:      ()     => apiFetch("/api/v1/admin/models/registry"),
  getDriftSnapshots:     (n)    => apiFetch(`/api/v1/admin/models/drift${n ? `?model_name=${n}` : ""}`),
};

export default { auth, patients, vitals, inference, agents, admin };
