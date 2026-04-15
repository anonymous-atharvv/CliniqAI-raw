import { useState, useEffect, useRef, useCallback } from "react";
import { vitals as vitalsApi, agents as agentsApi } from "../services/api";
import { subscribeToWard } from "../services/websocket";
import { RiskBadge } from "../components/RiskBadge";

// ── Design tokens ──────────────────────────────────────────────────────────────
const T = {
  bg:      "#050810",
  surface: "#0c1120",
  card:    "#131928",
  border:  "rgba(255,255,255,0.06)",
  text:    "#e8edf8",
  muted:   "#8e9bbf",
  dim:     "#4a5270",
  accent:  "#00d296",
  crit:    "#ff3d5a",
  high:    "#ff8c42",
  med:     "#f4c542",
  mono:    '"DM Mono", monospace',
  disp:    '"Syne", sans-serif',
};

const VITAL_LABELS = {
  heart_rate: "HR", spo2_pulse_ox: "SpO₂", bp_systolic: "SBP",
  respiratory_rate: "RR", temperature: "T°",
};

const VITAL_CRITS = {
  heart_rate:       { lo: 40,  hi: 150 },
  spo2_pulse_ox:    { lo: 85,  hi: null },
  bp_systolic:      { lo: 80,  hi: null },
  respiratory_rate: { lo: 6,   hi: 35 },
  temperature:      { lo: 34.0,hi: 40.0 },
};

const RISK_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

// ── Mini sparkline ─────────────────────────────────────────────────────────────
function Spark({ values = [], color = "#8e9bbf", w = 60, h = 24 }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || values.length < 2) return;
    const ctx = c.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    c.width = w * dpr; c.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const mn = Math.min(...values), mx = Math.max(...values);
    const rng = mx - mn || 1;
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - mn) / rng) * (h - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [values, color, w, h]);
  return <canvas ref={canvasRef} style={{ width: w, height: h, display: "block" }} />;
}

// ── Patient row ────────────────────────────────────────────────────────────────
function PatientRow({ patient, selected, onClick, liveVitals, sparklines }) {
  const risk = patient.risk_level || "LOW";
  const critBorder = risk === "CRITICAL" ? `1px solid rgba(255,61,90,0.35)` :
                     risk === "HIGH"     ? `1px solid rgba(255,140,66,0.25)` : `1px solid ${T.border}`;
  const critBg     = risk === "CRITICAL" ? "rgba(255,61,90,0.04)" :
                     risk === "HIGH"     ? "rgba(255,140,66,0.03)" : "transparent";

  const vitalOrder = ["heart_rate", "spo2_pulse_ox", "bp_systolic", "respiratory_rate", "temperature"];
  const lv = liveVitals[patient.bed_id] || {};

  return (
    <tr
      onClick={() => onClick(patient)}
      style={{
        cursor: "pointer",
        background: selected ? "rgba(0,210,150,0.05)" : critBg,
        borderBottom: critBorder,
        transition: "background 0.1s",
      }}
    >
      {/* Bed + Risk */}
      <td style={{ padding: "11px 14px", whiteSpace: "nowrap" }}>
        <div style={{ fontFamily: T.disp, fontWeight: 800, fontSize: 15, color: T.text }}>{patient.bed_id}</div>
        <div style={{ marginTop: 3 }}><RiskBadge level={risk} size="sm" /></div>
      </td>

      {/* Patient info */}
      <td style={{ padding: "11px 14px" }}>
        <div style={{ fontSize: 12, color: T.text }}>{patient.patient_summary || "—"}</div>
        <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{patient.scenario_name || ""}</div>
      </td>

      {/* News2 */}
      <td style={{ padding: "11px 14px", textAlign: "center" }}>
        <span style={{
          fontFamily: T.disp, fontWeight: 800, fontSize: 18,
          color: (patient.news2_score || 0) >= 7 ? T.crit : (patient.news2_score || 0) >= 5 ? T.high : T.text,
        }}>{patient.news2_score ?? "—"}</span>
      </td>

      {/* Sepsis */}
      <td style={{ padding: "11px 14px", textAlign: "center" }}>
        {patient.sepsis_probability_12h != null ? (
          <div>
            <span style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 15, color: patient.sepsis_probability_12h > 0.5 ? T.crit : T.muted }}>
              {(patient.sepsis_probability_12h * 100).toFixed(0)}%
            </span>
            <div style={{ width: 48, height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2, margin: "3px auto 0" }}>
              <div style={{ width: `${(patient.sepsis_probability_12h * 100).toFixed(0)}%`, height: "100%", background: patient.sepsis_probability_12h > 0.5 ? T.crit : T.high, borderRadius: 2 }} />
            </div>
          </div>
        ) : "—"}
      </td>

      {/* Live vitals */}
      {vitalOrder.map(param => {
        const val = lv[param] ?? (patient.latest_vitals || {})[param];
        const crit = VITAL_CRITS[param];
        const isBad = val != null && ((crit.lo != null && val < crit.lo) || (crit.hi != null && val > crit.hi));
        const spark = (sparklines[patient.bed_id] || {})[param] || [];
        return (
          <td key={param} style={{ padding: "11px 10px", textAlign: "center" }}>
            <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 14, color: isBad ? T.crit : T.text }}>
              {val != null ? (typeof val === "number" ? val.toFixed(param === "temperature" ? 1 : 0) : val) : "—"}
            </div>
            <div style={{ display: "flex", justifyContent: "center", marginTop: 3 }}>
              <Spark values={spark} color={isBad ? T.crit : T.dim} w={52} h={18} />
            </div>
          </td>
        );
      })}

      {/* Alerts */}
      <td style={{ padding: "11px 14px", textAlign: "center" }}>
        {(patient.escalations_sent || 0) > 0
          ? <span style={{ fontSize: 11, padding: "3px 8px", background: "rgba(255,61,90,0.1)", color: T.crit, borderRadius: 10, border: "1px solid rgba(255,61,90,0.3)" }}>🔔 {patient.escalations_sent}</span>
          : <span style={{ color: T.dim, fontSize: 12 }}>—</span>
        }
      </td>
    </tr>
  );
}

// ── Summary stats bar ──────────────────────────────────────────────────────────
function StatsBar({ patients }) {
  const counts = patients.reduce((acc, p) => {
    acc[p.risk_level] = (acc[p.risk_level] || 0) + 1;
    return acc;
  }, {});
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
      {[["CRITICAL", T.crit], ["HIGH", T.high], ["MEDIUM", T.med], ["LOW", T.accent]].map(([lv, c]) => (
        <div key={lv} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: c, display: "inline-block" }} />
          <span style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 16, color: c }}>{counts[lv] || 0}</span>
          <span style={{ fontSize: 10, color: T.dim, textTransform: "uppercase", letterSpacing: "0.05em" }}>{lv}</span>
        </div>
      ))}
      <span style={{ marginLeft: "auto", fontSize: 11, color: T.dim }}>{patients.length} patients monitored</span>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────
export default function ICUMonitor({ wardCode = "ICU-B", onSelectPatient }) {
  const [patients,    setPatients]    = useState([]);
  const [liveVitals,  setLiveVitals]  = useState({}); // { bed_id: { param: val } }
  const [sparklines,  setSparklines]  = useState({}); // { bed_id: { param: [vals] } }
  const [escalations, setEscalations] = useState([]);
  const [selected,    setSelected]    = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [filterRisk,  setFilterRisk]  = useState("ALL");
  const [sortBy,      setSortBy]      = useState("risk"); // risk | news2 | bed

  // Initial load + 30s polling
  const loadSnapshot = useCallback(async () => {
    try {
      const data = await vitalsApi.getWardSnapshot(wardCode);
      setPatients(data.patients || []);
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Ward snapshot error:", e);
    }
  }, [wardCode]);

  useEffect(() => {
    loadSnapshot();
    const timer = setInterval(loadSnapshot, 30000);
    return () => clearInterval(timer);
  }, [loadSnapshot]);

  // Live WebSocket stream
  useEffect(() => {
    const unsub = subscribeToWard(
      wardCode,
      (msg) => {
        setWsConnected(true);
        if (msg.type === "vitals_update" && msg.patients) {
          // Update live vitals + rolling sparklines
          setLiveVitals(prev => {
            const updated = { ...prev };
            msg.patients.forEach(p => {
              updated[p.bed_id] = { ...updated[p.bed_id], ...p };
            });
            return updated;
          });
          setSparklines(prev => {
            const updated = { ...prev };
            const MAX_SPARK = 30;
            msg.patients.forEach(p => {
              if (!updated[p.bed_id]) updated[p.bed_id] = {};
              ["heart_rate", "spo2", "bp_sys"].forEach(k => {
                if (p[k] == null) return;
                const paramMap = { heart_rate: "heart_rate", spo2: "spo2_pulse_ox", bp_sys: "bp_systolic" };
                const pk = paramMap[k];
                const arr = updated[p.bed_id][pk] || [];
                updated[p.bed_id][pk] = [...arr.slice(-MAX_SPARK + 1), p[k]];
              });
            });
            return updated;
          });
        }
      },
      () => setWsConnected(false),
    );
    return unsub;
  }, [wardCode]);

  // Active escalations
  useEffect(() => {
    agentsApi.getActiveEscalations(wardCode).then(setEscalations).catch(() => {});
    const t = setInterval(() => agentsApi.getActiveEscalations(wardCode).then(setEscalations).catch(() => {}), 15000);
    return () => clearInterval(t);
  }, [wardCode]);

  // Sort + filter
  const displayed = patients
    .filter(p => filterRisk === "ALL" || p.risk_level === filterRisk)
    .sort((a, b) => {
      if (sortBy === "risk")   return (RISK_ORDER[a.risk_level] ?? 4) - (RISK_ORDER[b.risk_level] ?? 4);
      if (sortBy === "news2")  return (b.news2_score ?? -1) - (a.news2_score ?? -1);
      if (sortBy === "bed")    return a.bed_id.localeCompare(b.bed_id);
      return 0;
    });

  const handleRowClick = (patient) => {
    setSelected(patient.bed_id);
    onSelectPatient?.(patient);
  };

  return (
    <div style={{ background: T.bg, minHeight: "100vh", fontFamily: T.mono, color: T.text, padding: "20px 24px" }}>

      {/* Page Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <div>
          <h1 style={{ fontFamily: T.disp, fontSize: 22, fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>
            ICU Monitor — {wardCode}
          </h1>
          <div style={{ fontSize: 11, color: T.dim, marginTop: 3 }}>
            {lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : "Loading…"}
            {" · "}
            <span style={{ color: wsConnected ? T.accent : T.high }}>
              {wsConnected ? "● Live" : "○ Polling"}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {/* Filter */}
          {["ALL", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map(f => (
            <button key={f} onClick={() => setFilterRisk(f)} style={{
              padding: "5px 12px", borderRadius: 6, border: `1px solid ${filterRisk === f ? "rgba(0,210,150,0.4)" : T.border}`,
              background: filterRisk === f ? "rgba(0,210,150,0.1)" : "transparent",
              color: filterRisk === f ? T.accent : T.muted, cursor: "pointer", fontSize: 11, fontFamily: T.mono,
            }}>{f}</button>
          ))}
          <button onClick={loadSnapshot} style={{ padding: "5px 12px", borderRadius: 6, border: `1px solid ${T.border}`, background: "transparent", color: T.muted, cursor: "pointer", fontSize: 11 }}>↻ Refresh</button>
        </div>
      </div>

      {/* Stats bar */}
      <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: "12px 18px", marginBottom: 16 }}>
        <StatsBar patients={patients} />
      </div>

      {/* Escalations banner */}
      {escalations.length > 0 && (
        <div style={{ padding: "10px 16px", background: "rgba(255,61,90,0.08)", border: "1px solid rgba(255,61,90,0.3)", borderRadius: 8, marginBottom: 14, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 18 }}>🚨</span>
          <span style={{ fontSize: 12, color: T.crit, fontWeight: 600 }}>
            {escalations.length} unacknowledged escalation{escalations.length > 1 ? "s" : ""} require attention
          </span>
          <span style={{ marginLeft: "auto", fontSize: 11, color: T.muted }}>
            {escalations[0]?.message?.slice(0, 80)}…
          </span>
        </div>
      )}

      {/* Patient table */}
      <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${T.border}` }}>
              {[
                { label: "Bed / Risk", key: "risk",  w: 110 },
                { label: "Patient",    key: null,     w: 140 },
                { label: "NEWS2",      key: "news2",  w: 70 },
                { label: "Sepsis",     key: null,     w: 80 },
                { label: "HR",         key: null,     w: 75 },
                { label: "SpO₂",       key: null,     w: 75 },
                { label: "SBP",        key: null,     w: 75 },
                { label: "RR",         key: null,     w: 75 },
                { label: "T°",         key: null,     w: 75 },
                { label: "Alerts",     key: null,     w: 80 },
              ].map(col => (
                <th key={col.label} onClick={() => col.key && setSortBy(col.key)}
                  style={{ padding: "10px 14px", textAlign: col.label === "Bed / Risk" || col.label === "Patient" ? "left" : "center", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.07em", color: sortBy === col.key ? T.accent : T.dim, cursor: col.key ? "pointer" : "default", userSelect: "none", width: col.w, fontFamily: T.mono, fontWeight: 600 }}>
                  {col.label}{sortBy === col.key ? " ↓" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayed.length === 0 ? (
              <tr>
                <td colSpan={10} style={{ textAlign: "center", padding: "40px", color: T.dim, fontSize: 13 }}>
                  {patients.length === 0 ? "Loading patients…" : "No patients match filter"}
                </td>
              </tr>
            ) : displayed.map(p => (
              <PatientRow
                key={p.bed_id}
                patient={p}
                selected={selected === p.bed_id}
                onClick={handleRowClick}
                liveVitals={liveVitals}
                sparklines={sparklines}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer note */}
      <div style={{ marginTop: 14, fontSize: 10, color: T.dim, textAlign: "center" }}>
        AI Decision Support Only · Physician review required for all clinical actions · NEWS2 thresholds: ≥7 Urgent · ≥5 High Alert
      </div>
    </div>
  );
}
