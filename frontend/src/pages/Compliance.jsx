import { useState, useEffect } from "react";
import { admin as adminApi } from "../services/api";

const T = {
  bg:     "#050810",
  surf:   "#0c1120",
  card:   "#131928",
  border: "rgba(255,255,255,0.06)",
  text:   "#e8edf8",
  muted:  "#8e9bbf",
  dim:    "#4a5270",
  accent: "#00d296",
  blue:   "#4d8fff",
  crit:   "#ff3d5a",
  high:   "#ff8c42",
  mono:   '"DM Mono", monospace',
  disp:   '"Syne", sans-serif',
};

// ── Mock audit log entries (replace with real API when audit log endpoint added) ──
const MOCK_AUDIT_EVENTS = [
  { id: "evt-001", ts: "2026-04-11T09:14:22Z", actor: "physician_001", role: "physician", action: "infer", resource: "ClinicalInference", outcome: "success", phi: false, dept: "ICU" },
  { id: "evt-002", ts: "2026-04-11T09:13:11Z", actor: "nurse_003",     role: "nurse",     action: "read",  resource: "Observation",       outcome: "success", phi: true,  dept: "ICU" },
  { id: "evt-003", ts: "2026-04-11T09:11:05Z", actor: "ai_system",     role: "ai_system", action: "read",  resource: "Observation",       outcome: "success", phi: false, dept: "AI" },
  { id: "evt-004", ts: "2026-04-11T09:08:44Z", actor: "physician_002", role: "physician", action: "read",  resource: "Patient",           outcome: "success", phi: true,  dept: "Emergency" },
  { id: "evt-005", ts: "2026-04-11T08:55:19Z", actor: "researcher_01", role: "researcher",action: "read",  resource: "Patient",           outcome: "denied",  phi: false, dept: "Research" },
  { id: "evt-006", ts: "2026-04-11T08:44:02Z", actor: "physician_001", role: "physician", action: "write", resource: "MedicationRequest",  outcome: "success", phi: true,  dept: "ICU" },
  { id: "evt-007", ts: "2026-04-11T08:30:00Z", actor: "pharmacist_01", role: "pharmacist",action: "read",  resource: "MedicationRequest",  outcome: "success", phi: false, dept: "Pharmacy" },
  { id: "evt-008", ts: "2026-04-11T08:12:38Z", actor: "admin_001",     role: "admin",     action: "read",  resource: "AdminResource",     outcome: "success", phi: false, dept: "Administration" },
];

const COMPLIANCE_STATUS = [
  { framework: "HIPAA Technical Safeguards",      status: "COMPLIANT",  detail: "AES-256, TLS 1.3, ABAC, WORM audit" },
  { framework: "HIPAA Administrative Safeguards", status: "COMPLIANT",  detail: "Policies, training, BAA executed" },
  { framework: "HIPAA Physical Safeguards",       status: "COMPLIANT",  detail: "AWS HIPAA-eligible services with BAA" },
  { framework: "BAA with Hospital",               status: "SIGNED",     detail: "Effective 2026-01-01" },
  { framework: "FDA SaMD Class II (510k)",        status: "PENDING",    detail: "Submission target Q3 2026" },
  { framework: "SOC 2 Type II",                   status: "IN_PROGRESS",detail: "Audit in progress — target Month 8" },
  { framework: "FHIR R4 Certification",           status: "COMPLIANT",  detail: "Validated vs HL7 test suite + Epic sandbox" },
  { framework: "PHI De-identification",           status: "COMPLIANT",  detail: "Safe Harbor 18 identifiers — all handled" },
];

const STATUS_COLORS = {
  COMPLIANT:    { bg: "rgba(0,210,150,0.08)",  text: "#00d296", border: "rgba(0,210,150,0.2)"  },
  SIGNED:       { bg: "rgba(0,210,150,0.08)",  text: "#00d296", border: "rgba(0,210,150,0.2)"  },
  PENDING:      { bg: "rgba(244,197,66,0.08)", text: "#f4c542", border: "rgba(244,197,66,0.2)" },
  IN_PROGRESS:  { bg: "rgba(77,143,255,0.08)", text: "#4d8fff", border: "rgba(77,143,255,0.2)" },
  NON_COMPLIANT:{ bg: "rgba(255,61,90,0.08)",  text: "#ff3d5a", border: "rgba(255,61,90,0.2)"  },
};

const ACTION_ICONS = { read: "👁", write: "✏️", infer: "🤖", export: "📤", delete: "🗑" };
const OUTCOME_COLORS = { success: T.accent, denied: "#ff3d5a" };

// ── Sub-components ─────────────────────────────────────────────────────────────
function Panel({ title, tag, children }) {
  return (
    <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 14 }}>
      <div style={{ padding: "13px 18px 11px", borderBottom: `1px solid ${T.border}`, display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontFamily: T.disp, fontSize: 13, fontWeight: 700 }}>{title}</span>
        {tag && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "rgba(77,143,255,0.1)", color: T.blue, border: "1px solid rgba(77,143,255,0.2)" }}>{tag}</span>}
      </div>
      <div style={{ padding: "14px 18px" }}>{children}</div>
    </div>
  );
}

function StatusBadge({ status }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.PENDING;
  return <span style={{ fontSize: 10, padding: "2px 9px", background: s.bg, color: s.text, border: `1px solid ${s.border}`, borderRadius: 10, fontWeight: 600, letterSpacing: "0.04em" }}>{status}</span>;
}

function AuditRow({ event }) {
  const isWarning = event.outcome === "denied" || event.phi;
  return (
    <tr style={{ borderBottom: `1px solid ${T.border}`, background: isWarning && event.outcome === "denied" ? "rgba(255,61,90,0.03)" : "transparent" }}>
      <td style={{ padding: "8px 12px", fontFamily: T.mono, fontSize: 10, color: T.dim, whiteSpace: "nowrap" }}>
        {new Date(event.ts).toLocaleTimeString()}
      </td>
      <td style={{ padding: "8px 12px", fontSize: 11, color: T.muted }}>{event.actor}</td>
      <td style={{ padding: "8px 12px" }}>
        <span style={{ fontSize: 10, padding: "2px 7px", background: "rgba(255,255,255,0.04)", color: T.muted, borderRadius: 4 }}>{event.role}</span>
      </td>
      <td style={{ padding: "8px 12px", textAlign: "center" }}>
        <span title={event.action}>{ACTION_ICONS[event.action] || "📋"}</span>
        <span style={{ fontSize: 10, color: T.dim, marginLeft: 4 }}>{event.action}</span>
      </td>
      <td style={{ padding: "8px 12px", fontSize: 11, color: T.muted }}>{event.resource}</td>
      <td style={{ padding: "8px 12px", textAlign: "center" }}>
        {event.phi
          ? <span style={{ fontSize: 10, padding: "2px 7px", background: "rgba(244,197,66,0.1)", color: "#f4c542", borderRadius: 4 }}>PHI</span>
          : <span style={{ fontSize: 10, color: T.dim }}>—</span>
        }
      </td>
      <td style={{ padding: "8px 12px" }}>
        <span style={{ fontSize: 10, padding: "2px 8px", background: event.outcome === "success" ? "rgba(0,210,150,0.08)" : "rgba(255,61,90,0.1)", color: OUTCOME_COLORS[event.outcome], borderRadius: 4 }}>
          {event.outcome}
        </span>
      </td>
    </tr>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────
export default function Compliance() {
  const [models, setModels] = useState([]);
  const [drift,  setDrift]  = useState(null);
  const [auditFilter, setAuditFilter] = useState("all");    // all | denied | phi
  const [activeTab, setTab] = useState("status");            // status | audit | governance | deident

  useEffect(() => {
    Promise.allSettled([adminApi.getModelRegistry(), adminApi.getDriftSnapshots()])
      .then(([m, d]) => {
        if (m.status === "fulfilled") setModels(m.value?.models || []);
        if (d.status === "fulfilled") setDrift(d.value);
      });
  }, []);

  const filteredAudit = MOCK_AUDIT_EVENTS.filter(e => {
    if (auditFilter === "denied") return e.outcome === "denied";
    if (auditFilter === "phi")    return e.phi;
    return true;
  });

  const TABS = ["status", "audit", "governance", "deident"];

  return (
    <div style={{ background: T.bg, minHeight: "100vh", fontFamily: T.mono, color: T.text, padding: "20px 28px" }}>

      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontFamily: T.disp, fontSize: 22, fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>
          Compliance & Governance
        </h1>
        <div style={{ fontSize: 11, color: T.dim, marginTop: 4 }}>HIPAA · FDA SaMD · Audit Logs · Model Governance</div>
      </div>

      {/* Quick summary badges */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 20 }}>
        {[
          { label: "HIPAA Technical Safeguards", color: "accent" },
          { label: "BAA Signed",                 color: "accent" },
          { label: "PHI De-identification",      color: "accent" },
          { label: "FDA 510k Pending",           color: "med"    },
          { label: "SOC 2 In Progress",          color: "blue"   },
        ].map(b => (
          <span key={b.label} style={{ fontSize: 11, padding: "5px 12px", background: `rgba(${b.color === "accent" ? "0,210,150" : b.color === "med" ? "244,197,66" : "77,143,255"},0.08)`, color: T[b.color], border: `1px solid rgba(${b.color === "accent" ? "0,210,150" : b.color === "med" ? "244,197,66" : "77,143,255"},0.2)`, borderRadius: 20 }}>
            {b.color === "accent" ? "✓" : "○"} {b.label}
          </span>
        ))}
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 2, marginBottom: 18, background: T.surf, border: `1px solid ${T.border}`, borderRadius: 8, padding: 4 }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            flex: 1, padding: "9px", border: "none", borderRadius: 6, cursor: "pointer", fontFamily: T.mono, fontSize: 12, fontWeight: activeTab === t ? 600 : 400, textTransform: "capitalize",
            background: activeTab === t ? "rgba(0,210,150,0.1)" : "transparent",
            color: activeTab === t ? T.accent : T.muted,
          }}>{t}</button>
        ))}
      </div>

      {/* ── STATUS TAB ── */}
      {activeTab === "status" && (
        <Panel title="Compliance Status" tag="HIPAA · FDA · SOC 2">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {COMPLIANCE_STATUS.map(row => (
              <div key={row.framework} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 12px", background: "rgba(255,255,255,0.02)", border: `1px solid ${T.border}`, borderRadius: 7 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: T.text, marginBottom: 3 }}>{row.framework}</div>
                  <div style={{ fontSize: 11, color: T.muted }}>{row.detail}</div>
                </div>
                <StatusBadge status={row.status} />
              </div>
            ))}
          </div>
        </Panel>
      )}

      {/* ── AUDIT TAB ── */}
      {activeTab === "audit" && (
        <Panel title="Audit Log" tag="HIPAA §164.312(b)">
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            <span style={{ fontSize: 11, color: T.muted, alignSelf: "center" }}>Filter:</span>
            {["all", "denied", "phi"].map(f => (
              <button key={f} onClick={() => setAuditFilter(f)} style={{ padding: "5px 12px", borderRadius: 6, border: `1px solid ${auditFilter === f ? "rgba(0,210,150,0.4)" : T.border}`, background: auditFilter === f ? "rgba(0,210,150,0.1)" : "transparent", color: auditFilter === f ? T.accent : T.muted, cursor: "pointer", fontSize: 11, fontFamily: T.mono }}>
                {f === "all" ? "All events" : f === "denied" ? "Denied access" : "PHI access"}
              </button>
            ))}
            <span style={{ marginLeft: "auto", fontSize: 11, color: T.dim }}>
              {filteredAudit.length} events · Retained 6 years (HIPAA)
            </span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                  {["Time", "Actor", "Role", "Action", "Resource", "PHI", "Outcome"].map(h => (
                    <th key={h} style={{ padding: "8px 12px", textAlign: h === "Action" || h === "PHI" || h === "Outcome" ? "center" : "left", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.07em", color: T.dim, fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredAudit.map(e => <AuditRow key={e.id} event={e} />)}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 12, padding: "10px 12px", background: "rgba(77,143,255,0.05)", border: "1px solid rgba(77,143,255,0.15)", borderRadius: 7, fontSize: 11, color: T.dim }}>
            Audit log is immutable. Events are written to PostgreSQL (queryable) and S3 WORM bucket simultaneously. 
            Six-year minimum retention per HIPAA §164.312(b). No audit events can be deleted or modified.
          </div>
        </Panel>
      )}

      {/* ── GOVERNANCE TAB ── */}
      {activeTab === "governance" && (
        <>
          <Panel title="AI Model Registry">
            {models.length === 0 ? (
              <div style={{ color: T.dim, fontSize: 12 }}>Loading model registry…</div>
            ) : models.map((m, i) => (
              <div key={i} style={{ padding: "12px 0", borderBottom: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                  <span style={{ fontFamily: T.mono, fontSize: 12, fontWeight: 600, color: T.text, flex: 1 }}>{m.name}</span>
                  <span style={{ fontSize: 10, padding: "2px 8px", background: m.deployed ? "rgba(0,210,150,0.1)" : "rgba(255,255,255,0.04)", color: m.deployed ? T.accent : T.muted, borderRadius: 4 }}>
                    {m.deployed ? "DEPLOYED" : "NOT DEPLOYED"}
                  </span>
                  <span style={{ fontSize: 10, color: T.dim }}>{m.fda_status}</span>
                </div>
                <div style={{ display: "flex", gap: 16, fontSize: 11, color: T.muted, flexWrap: "wrap" }}>
                  <span>Version: <strong style={{ color: T.text }}>{m.version}</strong></span>
                  {m.validation_auroc && <span>Validation AUROC: <strong style={{ color: T.accent }}>{m.validation_auroc}</strong></span>}
                  <span>Type: {m.type}</span>
                  <span>Hospitals: {(m.deployed_hospitals || []).join(", ") || "—"}</span>
                </div>
              </div>
            ))}
          </Panel>

          {drift && (
            <Panel title="Model Drift Monitoring" tag="Weekly">
              <div style={{ display: "flex", gap: 16, marginBottom: 14, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontSize: 10, color: T.dim, marginBottom: 3 }}>Model</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>{drift.model}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: T.dim, marginBottom: 3 }}>Baseline AUROC</div>
                  <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 20, color: T.accent }}>{drift.baseline_auroc}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: T.dim, marginBottom: 3 }}>Auto-updates frozen</div>
                  <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 14, color: drift.auto_updates_frozen ? T.crit : T.accent }}>
                    {drift.auto_updates_frozen ? "YES" : "No"}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: T.dim, marginBottom: 3 }}>Next review</div>
                  <div style={{ fontSize: 12, color: T.muted }}>{drift.next_review_date}</div>
                </div>
              </div>
              <div style={{ padding: "10px 12px", background: "rgba(255,255,255,0.02)", border: `1px solid ${T.border}`, borderRadius: 7, fontSize: 11, color: T.dim, lineHeight: 1.6 }}>
                <strong style={{ color: T.text }}>Drift policy:</strong> If AUROC drops more than 5% from baseline, AI model auto-updates are frozen. A minimum 2% improvement must be demonstrated before deploying any model update. All updates require clinical review board sign-off.
              </div>
            </Panel>
          )}
        </>
      )}

      {/* ── DE-IDENTIFICATION TAB ── */}
      {activeTab === "deident" && (
        <Panel title="PHI De-Identification — HIPAA Safe Harbor §164.514(b)">
          <div style={{ marginBottom: 14, padding: "10px 14px", background: "rgba(0,210,150,0.05)", border: "1px solid rgba(0,210,150,0.15)", borderRadius: 7, fontSize: 12, color: T.muted, lineHeight: 1.7 }}>
            All 18 Safe Harbor identifiers are removed or transformed before any AI processing. <strong style={{ color: T.text }}>Patient data never reaches external LLM APIs in identifiable form.</strong>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            {[
              ["Names",                   "→ UUID pseudonym (HMAC-SHA256)",            "COMPLIANT"],
              ["Geographic data (< state)","→ State + 3-digit ZIP prefix only",         "COMPLIANT"],
              ["Dates (except year)",     "→ Shifted ±90 days (consistent per patient)", "COMPLIANT"],
              ["Phone numbers",           "→ Removed entirely",                         "COMPLIANT"],
              ["Fax numbers",             "→ Removed entirely",                         "COMPLIANT"],
              ["Email addresses",         "→ Removed entirely",                         "COMPLIANT"],
              ["SSN",                     "→ UUID pseudonym (HMAC-SHA256)",             "COMPLIANT"],
              ["Medical record numbers",  "→ UUID pseudonym",                           "COMPLIANT"],
              ["Health plan numbers",     "→ UUID pseudonym or removed",                "COMPLIANT"],
              ["Account numbers",         "→ UUID pseudonym or removed",                "COMPLIANT"],
              ["Certificate/license #",   "→ Removed",                                  "COMPLIANT"],
              ["Vehicle identifiers",     "→ Removed",                                  "COMPLIANT"],
              ["Device identifiers",      "→ Device pseudonym",                         "COMPLIANT"],
              ["Web URLs",                "→ Removed",                                  "COMPLIANT"],
              ["IP addresses",            "→ SHA-256 hashed",                           "COMPLIANT"],
              ["Biometric identifiers",   "→ Hashed with patient-specific salt",        "COMPLIANT"],
              ["Full-face photographs",   "→ Not stored in AI layer",                   "COMPLIANT"],
              ["Other unique identifiers","→ UUID pseudonym",                           "COMPLIANT"],
            ].map(([id, treatment, status]) => (
              <div key={id} style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", border: `1px solid ${T.border}`, borderRadius: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: T.text, flex: 1 }}>{id}</span>
                  <StatusBadge status={status} />
                </div>
                <div style={{ fontSize: 10, color: T.dim, marginTop: 3, fontStyle: "italic" }}>{treatment}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14, padding: "10px 12px", background: "rgba(255,255,255,0.02)", border: `1px solid ${T.border}`, borderRadius: 7, fontSize: 11, color: T.dim, lineHeight: 1.6 }}>
            <strong style={{ color: T.text }}>Preserved under Safe Harbor:</strong> Birth year, state of residence, clinical codes (ICD-10, SNOMED, LOINC, RxNorm), vital sign values. The original-to-pseudonym mapping is stored in HashiCorp Vault with separate access controls — never co-located with AI processing systems.
          </div>
        </Panel>
      )}
    </div>
  );
}
