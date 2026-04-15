import { useState, useEffect, useCallback } from "react";
import { patients as patientsApi, inference as inferenceApi, agents as agentsApi } from "../services/api";
import { subscribeToPatient } from "../services/websocket";
import { RiskBadge } from "../components/RiskBadge";
import { VitalsChart } from "../components/VitalsChart";
import { AIRecommendation } from "../components/AIRecommendation";
import { AgentStatus } from "../components/AgentStatus";
import { FeedbackButton } from "../components/RiskBadge";

// ── Design tokens ─────────────────────────────────────────────────────────────
const T = {
  bg:       "var(--bg-base,       #050810)",
  surface:  "var(--bg-card,       #131928)",
  border:   "var(--border-dim,    rgba(255,255,255,0.06))",
  text:     "var(--text-primary,  #e8edf8)",
  muted:    "var(--text-secondary,#8e9bbf)",
  dim:      "var(--text-dim,      #4a5270)",
  accent:   "var(--accent,        #00d296)",
  blue:     "var(--accent-blue,   #4d8fff)",
  crit:     "var(--critical,      #ff3d5a)",
  high:     "var(--high,          #ff8c42)",
  medium:   "var(--medium,        #f4c542)",
  fontMono: '"DM Mono", monospace',
  fontDisp: '"Syne", sans-serif',
  fontSrif: '"Instrument Serif", serif',
};

// ── Sub-components ─────────────────────────────────────────────────────────────

function Section({ title, tag, children, collapsible = false }) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 14 }}>
      <div
        onClick={() => collapsible && setOpen(o => !o)}
        style={{ padding: "13px 18px 11px", borderBottom: open ? `1px solid ${T.border}` : "none", display: "flex", alignItems: "center", justifyContent: "space-between", cursor: collapsible ? "pointer" : "default" }}
      >
        <span style={{ fontFamily: T.fontDisp, fontSize: 13, fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }}>
          {title}
          {tag && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "rgba(0,210,150,0.1)", color: T.accent, border: `1px solid rgba(0,210,150,0.2)`, fontFamily: T.fontMono, textTransform: "uppercase", letterSpacing: "0.05em" }}>{tag}</span>}
        </span>
        {collapsible && <span style={{ color: T.dim, fontSize: 12 }}>{open ? "▲" : "▼"}</span>}
      </div>
      {open && <div style={{ padding: "14px 18px" }}>{children}</div>}
    </div>
  );
}

function VitalRow({ label, value, unit, critLow, critHigh, baselineMin, baselineMax }) {
  const v = parseFloat(value);
  const isCritLow  = critLow  != null && v < critLow;
  const isCritHigh = critHigh != null && v > critHigh;
  const isCrit = isCritLow || isCritHigh;
  const isAbnorm = !isCrit && baselineMin != null && (v < baselineMin || v > baselineMax);
  const color = isCrit ? T.crit : isAbnorm ? T.high : T.text;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0", borderBottom: `1px solid ${T.border}` }}>
      <span style={{ width: 140, fontSize: 11, color: T.muted }}>{label}</span>
      <span style={{ fontFamily: T.fontDisp, fontSize: 18, fontWeight: 700, color, minWidth: 60 }}>{value}</span>
      <span style={{ fontSize: 11, color: T.dim }}>{unit}</span>
      {isCrit && <span style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px", background: "rgba(255,61,90,0.12)", color: T.crit, border: "1px solid rgba(255,61,90,0.3)", borderRadius: 10 }}>⚠ CRITICAL</span>}
      {isAbnorm && !isCrit && <span style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px", background: "rgba(255,140,66,0.1)", color: T.high, border: "1px solid rgba(255,140,66,0.2)", borderRadius: 10 }}>↑ ABNORMAL</span>}
    </div>
  );
}

function TimelineEvent({ event }) {
  const icons = { admission: "🏥", ai_alert: "🤖", lab_result: "🧪", medication: "💊", physician_action: "👨‍⚕️", imaging: "🩻" };
  const colors = { ai_alert: T.high, admission: T.blue };
  return (
    <div style={{ display: "flex", gap: 12, paddingBottom: 14, position: "relative" }}>
      <div style={{ width: 28, height: 28, borderRadius: "50%", background: "rgba(255,255,255,0.04)", border: `1px solid ${T.border}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, flexShrink: 0 }}>
        {icons[event.type] || "📋"}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 12, color: colors[event.type] || T.muted, lineHeight: 1.5 }}>{event.description}</div>
        <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{new Date(event.time).toLocaleTimeString()}</div>
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

export default function PatientView({ patientId, onBack }) {
  const [patient,    setPatient]    = useState(null);
  const [vitals,     setVitals]     = useState({});
  const [liveVitals, setLiveVitals] = useState({});
  const [intel,      setIntel]      = useState(null);
  const [timeline,   setTimeline]   = useState([]);
  const [meds,       setMeds]       = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [inferLoading, setInferLoading] = useState(false);
  const [activeTab,  setActiveTab]  = useState("overview"); // overview | reasoning | timeline | meds

  // Fetch all patient data in parallel
  useEffect(() => {
    if (!patientId) return;
    setLoading(true);

    Promise.allSettled([
      patientsApi.get(patientId),
      patientsApi.getVitals(patientId, 6),
      patientsApi.getTimeline(patientId, 24),
      patientsApi.getMedications(patientId),
    ]).then(([pat, vit, tl, med]) => {
      if (pat.status === "fulfilled") setPatient(pat.value);
      if (vit.status === "fulfilled") {
        // Group vitals by parameter for chart
        const grouped = {};
        for (const r of (vit.value?.vitals || [])) {
          if (!grouped[r.parameter]) grouped[r.parameter] = [];
          grouped[r.parameter].push(r.value);
        }
        setVitals(grouped);
      }
      if (tl.status === "fulfilled")  setTimeline(tl.value?.events || []);
      if (med.status === "fulfilled") setMeds(med.value?.medications || []);
      setLoading(false);
    });
  }, [patientId]);

  // Live vitals via WebSocket
  useEffect(() => {
    if (!patientId) return;
    const unsub = subscribeToPatient(patientId, (msg) => {
      if (msg.type === "vital_reading" && msg.readings) {
        setLiveVitals(prev => ({ ...prev, ...msg.readings }));
      }
    });
    return unsub;
  }, [patientId]);

  // Load AI inference
  const loadIntelligence = useCallback(async () => {
    if (!patientId) return;
    setInferLoading(true);
    try {
      const data = await patientsApi.getIntelligence(patientId);
      setIntel(data);
    } catch (e) {
      console.error("Intelligence fetch failed:", e);
    } finally {
      setInferLoading(false);
    }
  }, [patientId]);

  useEffect(() => { loadIntelligence(); }, [loadIntelligence]);

  const TABS = [
    { id: "overview",  label: "Overview" },
    { id: "reasoning", label: "AI Reasoning" },
    { id: "timeline",  label: "Timeline" },
    { id: "meds",      label: "Medications" },
  ];

  const displayVitals = { ...liveVitals };

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 400, color: T.muted, fontFamily: T.fontMono }}>
        ⚕ Loading patient data…
      </div>
    );
  }

  return (
    <div style={{ fontFamily: T.fontMono, color: T.text, maxWidth: 1100, margin: "0 auto", padding: "0 0 40px" }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, marginBottom: 20 }}>
        <button onClick={onBack} style={{ background: "transparent", border: `1px solid ${T.border}`, color: T.muted, borderRadius: 6, padding: "7px 12px", cursor: "pointer", fontSize: 12 }}>
          ← Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <h1 style={{ fontFamily: T.fontDisp, fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
              {patient?.full_name || `Patient ${patientId?.slice(0, 8)}`}
            </h1>
            {intel && <RiskBadge level={intel.risk_level} size="md" />}
          </div>
          <div style={{ fontSize: 11, color: T.muted, marginTop: 4, display: "flex", gap: 16 }}>
            {patient?.date_of_birth && <span>DOB: {patient.date_of_birth}</span>}
            {patient?.gender        && <span>Gender: {patient.gender}</span>}
            {patient?.mrn           && <span>MRN: {patient.mrn}</span>}
          </div>
        </div>
        <button
          onClick={loadIntelligence}
          disabled={inferLoading}
          style={{ background: "rgba(0,210,150,0.1)", border: `1px solid rgba(0,210,150,0.3)`, color: T.accent, borderRadius: 8, padding: "8px 16px", cursor: inferLoading ? "not-allowed" : "pointer", fontSize: 12, fontFamily: T.fontMono }}
        >
          {inferLoading ? "⏳ Analyzing…" : "↻ Refresh AI"}
        </button>
      </div>

      {/* AI disclaimer */}
      <div style={{ padding: "8px 14px", background: "rgba(77,143,255,0.05)", border: `1px solid rgba(77,143,255,0.15)`, borderRadius: 7, marginBottom: 16, fontSize: 11, color: T.dim }}>
        <strong style={{ color: T.blue }}>AI Decision Support Only.</strong> All recommendations require physician review before clinical action.
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 2, marginBottom: 16, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: 4 }}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              flex: 1, padding: "8px", borderRadius: 6, border: "none", cursor: "pointer", fontFamily: T.fontMono, fontSize: 12, fontWeight: 500,
              background: activeTab === tab.id ? "rgba(0,210,150,0.1)" : "transparent",
              color: activeTab === tab.id ? T.accent : T.muted,
              transition: "all 0.15s",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "overview" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>

          {/* Live Vitals */}
          <div style={{ gridColumn: "1 / -1" }}>
            <Section title="Live Vital Signs" tag="1Hz">
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginBottom: 16 }}>
                {[
                  { key: "heart_rate",       label: "Heart Rate",   unit: "bpm",  cLo: 40,  cHi: 150 },
                  { key: "spo2_pulse_ox",    label: "SpO₂",         unit: "%",    cLo: 85,  cHi: null },
                  { key: "bp_systolic",      label: "BP Systolic",  unit: "mmHg", cLo: 80,  cHi: 220 },
                  { key: "respiratory_rate", label: "Resp Rate",    unit: "/min", cLo: 6,   cHi: 35  },
                  { key: "temperature",      label: "Temperature",  unit: "°C",   cLo: 34.0,cHi: 40.0 },
                ].map(v => {
                  const val = displayVitals[v.key];
                  const isCrit = val != null && ((v.cLo && val < v.cLo) || (v.cHi && val > v.cHi));
                  return (
                    <div key={v.key} style={{ textAlign: "center", padding: "12px 8px", background: isCrit ? "rgba(255,61,90,0.06)" : "rgba(255,255,255,0.02)", border: `1px solid ${isCrit ? "rgba(255,61,90,0.3)" : T.border}`, borderRadius: 8 }}>
                      <div style={{ fontFamily: T.fontDisp, fontSize: 24, fontWeight: 800, color: isCrit ? T.crit : T.text }}>
                        {val != null ? (typeof val === "number" ? val.toFixed(v.key === "temperature" ? 1 : 0) : val) : "—"}
                      </div>
                      <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{v.label}</div>
                      <div style={{ fontSize: 9, color: T.dim }}>{v.unit}</div>
                    </div>
                  );
                })}
              </div>
              <VitalsChart vitalsHistory={vitals} height={160} />
            </Section>
          </div>

          {/* AI Scores */}
          <Section title="AI Risk Scores" tag="AI">
            {intel ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {[
                  { label: "Overall Risk",     value: intel.risk_level,                 isLabel: true },
                  { label: "Deterioration 6h", value: intel.risk?.ai_predictions?.deterioration_6h, isPct: true },
                  { label: "Sepsis 12h",        value: intel.risk?.ai_predictions?.sepsis_12h,       isPct: true },
                  { label: "Mortality 24h",     value: intel.risk?.ai_predictions?.mortality_24h,    isPct: true },
                  { label: "NEWS2 Score",       value: intel.risk?.news2_score,         isNum: true  },
                  { label: "SOFA Score",        value: intel.risk?.sofa_score,          isNum: true  },
                ].map(row => (
                  <div key={row.label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: `1px solid ${T.border}` }}>
                    <span style={{ fontSize: 12, color: T.muted }}>{row.label}</span>
                    {row.isLabel && <RiskBadge level={row.value} size="sm" />}
                    {row.isPct && (
                      <div style={{ textAlign: "right" }}>
                        <span style={{ fontFamily: T.fontDisp, fontWeight: 700, fontSize: 16, color: row.value > 0.6 ? T.crit : row.value > 0.3 ? T.high : T.text }}>
                          {row.value != null ? `${(row.value * 100).toFixed(0)}%` : "—"}
                        </span>
                        {row.value != null && (
                          <div style={{ width: 80, height: 3, background: "rgba(255,255,255,0.05)", borderRadius: 2, marginTop: 3 }}>
                            <div style={{ width: `${(row.value * 100).toFixed(0)}%`, height: "100%", background: row.value > 0.6 ? T.crit : row.value > 0.3 ? T.high : T.accent, borderRadius: 2 }} />
                          </div>
                        )}
                      </div>
                    )}
                    {row.isNum && <span style={{ fontFamily: T.fontDisp, fontWeight: 700, fontSize: 18, color: T.text }}>{row.value ?? "—"}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: T.dim, fontSize: 12, textAlign: "center", padding: 20 }}>{inferLoading ? "Loading…" : "No data"}</div>
            )}
          </Section>

          {/* Agent Status */}
          <Section title="Agent Pipeline">
            <AgentStatus compact={false} />
          </Section>
        </div>
      )}

      {activeTab === "reasoning" && (
        <Section title="AI Clinical Reasoning" tag="AI">
          <AIRecommendation
            data={intel}
            inferenceId={intel?.session_id}
            loading={inferLoading}
          />
        </Section>
      )}

      {activeTab === "timeline" && (
        <Section title="Clinical Timeline (24h)">
          {timeline.length === 0
            ? <div style={{ color: T.dim, fontSize: 12 }}>No events recorded.</div>
            : timeline.map((e, i) => <TimelineEvent key={i} event={e} />)
          }
        </Section>
      )}

      {activeTab === "meds" && (
        <Section title="Active Medications">
          {meds.length === 0
            ? <div style={{ color: T.dim, fontSize: 12 }}>No active medications.</div>
            : meds.map((m, i) => (
              <div key={i} style={{ display: "flex", gap: 10, padding: "9px 0", borderBottom: `1px solid ${T.border}`, alignItems: "center" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: T.text, flex: 1 }}>{m.name}</span>
                <span style={{ fontSize: 11, color: T.muted }}>{m.dose} {m.route} {m.frequency}</span>
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "rgba(0,210,150,0.08)", color: T.accent }}>
                  {m.status}
                </span>
              </div>
            ))
          }
          {meds.length > 0 && (
            <div style={{ marginTop: 12, padding: "8px 12px", background: "rgba(77,143,255,0.05)", border: `1px solid rgba(77,143,255,0.15)`, borderRadius: 6, fontSize: 11, color: T.dim }}>
              Pharmacist agent monitors for drug-drug interactions and renal dose adjustments on every medication order.
            </div>
          )}
        </Section>
      )}
    </div>
  );
}
