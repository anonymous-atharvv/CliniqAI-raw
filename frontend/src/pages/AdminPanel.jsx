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
  med:    "#f4c542",
  mono:   '"DM Mono", monospace',
  disp:   '"Syne", sans-serif',
};

// ── Helpers ────────────────────────────────────────────────────────────────────
function fmt$  (n) { return n >= 1e6 ? `$${(n/1e6).toFixed(2)}M` : `$${(n/1000).toFixed(0)}K`; }
function fmtPct(n) { return `${(n * 100).toFixed(1)}%`; }

// ── Sub-components ─────────────────────────────────────────────────────────────
function Card({ title, children, colspan = 1 }) {
  return (
    <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 10, padding: "16px 20px", gridColumn: `span ${colspan}` }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: T.dim, fontWeight: 600, marginBottom: 14 }}>{title}</div>
      {children}
    </div>
  );
}

function KPI({ label, value, sub, delta, color }) {
  const dc = delta > 0 ? T.accent : delta < 0 ? T.crit : T.muted;
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontFamily: T.disp, fontSize: 26, fontWeight: 800, color: color || T.text, letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: 11, color: T.muted, marginTop: 2 }}>{label}</div>
      {sub   && <div style={{ fontSize: 10, color: T.dim, marginTop: 2 }}>{sub}</div>}
      {delta != null && <div style={{ fontSize: 11, color: dc, marginTop: 3 }}>{delta > 0 ? "▲" : "▼"} vs benchmark</div>}
    </div>
  );
}

function QualityRow({ id, name, rate, benchmark, patients, approaching }) {
  const pass = rate >= benchmark;
  const pct = (rate * 100).toFixed(1);
  const benchPct = (benchmark * 100).toFixed(0);
  return (
    <div style={{ padding: "9px 0", borderBottom: `1px solid ${T.border}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
        <span style={{ fontFamily: '"DM Mono", monospace', fontSize: 10, padding: "2px 7px", background: "rgba(77,143,255,0.1)", color: T.blue, borderRadius: 4, letterSpacing: "0.04em" }}>{id}</span>
        <span style={{ fontSize: 12, color: T.text, flex: 1 }}>{name}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: pass ? T.accent : T.crit }}>{pct}%</span>
        <span style={{ fontSize: 10, color: T.dim }}>target {benchPct}%</span>
        {approaching > 0 && (
          <span style={{ fontSize: 10, padding: "2px 7px", background: "rgba(244,197,66,0.1)", color: T.med, borderRadius: 4 }}>⚠ {approaching} due</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 2 }}>
        <div style={{ width: `${pct}%`, height: 4, background: pass ? T.accent : T.crit, borderRadius: 2, maxWidth: "100%", transition: "width 0.4s" }} />
        <div style={{ flex: 1, height: 4, background: "rgba(255,255,255,0.04)", borderRadius: 2 }} />
      </div>
      <div style={{ fontSize: 10, color: T.dim, marginTop: 3 }}>{patients} eligible patients</div>
    </div>
  );
}

function HRRPRow({ condition, data }) {
  const pass = data.readmission_rate < data.national_avg;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: `1px solid ${T.border}` }}>
      <span style={{ fontFamily: T.mono, fontSize: 10, padding: "2px 7px", background: "rgba(255,255,255,0.04)", color: T.muted, borderRadius: 4, letterSpacing: "0.04em", minWidth: 50 }}>{condition}</span>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", gap: 2 }}>
          <div style={{ width: `${data.readmission_rate * 400}px`, maxWidth: 120, height: 6, background: pass ? T.accent : T.crit, borderRadius: 2 }} />
        </div>
      </div>
      <span style={{ fontSize: 12, fontWeight: 700, color: pass ? T.accent : T.crit, minWidth: 44, textAlign: "right" }}>{fmtPct(data.readmission_rate)}</span>
      <span style={{ fontSize: 10, color: T.dim, minWidth: 50 }}>avg {fmtPct(data.national_avg)}</span>
      <span style={{ fontSize: 10, padding: "2px 7px", background: pass ? "rgba(0,210,150,0.08)" : "rgba(255,61,90,0.08)", color: pass ? T.accent : T.crit, borderRadius: 10 }}>{pass ? "✓ Below" : "↑ Above"}</span>
    </div>
  );
}

function ModelRow({ model }) {
  return (
    <div style={{ padding: "10px 0", borderBottom: `1px solid ${T.border}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: T.text, flex: 1 }}>{model.name}</span>
        <span style={{ fontSize: 10, padding: "2px 7px", background: model.deployed ? "rgba(0,210,150,0.1)" : "rgba(255,255,255,0.04)", color: model.deployed ? T.accent : T.muted, borderRadius: 4 }}>{model.deployed ? "DEPLOYED" : "STAGED"}</span>
        <span style={{ fontSize: 10, color: T.dim }}>{model.fda_status}</span>
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: 11, color: T.muted }}>
        <span>v{model.version}</span>
        {model.validation_auroc && <span>AUROC: <strong style={{ color: T.text }}>{model.validation_auroc}</strong></span>}
        <span>Type: {model.type}</span>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────
export default function AdminPanel() {
  const [cfo,       setCFO]     = useState(null);
  const [coo,       setCOO]     = useState(null);
  const [quality,   setQuality] = useState(null);
  const [readmit,   setReadmit] = useState(null);
  const [models,    setModels]  = useState(null);
  const [drift,     setDrift]   = useState(null);
  const [loading,   setLoading] = useState(true);
  const [activeTab, setTab]     = useState("financial"); // financial | quality | operations | models

  useEffect(() => {
    setLoading(true);
    Promise.allSettled([
      adminApi.getCFODashboard(),
      adminApi.getCOODashboard(),
      adminApi.getQualityMeasures(),
      adminApi.getCMSReport("2026-04"),
      adminApi.getModelRegistry(),
      adminApi.getDriftSnapshots(),
    ]).then(([c, co, q, r, m, d]) => {
      if (c.status  === "fulfilled") setCFO(c.value);
      if (co.status === "fulfilled") setCOO(co.value);
      if (q.status  === "fulfilled") setQuality(q.value);
      if (r.status  === "fulfilled") setReadmit(r.value);
      if (m.status  === "fulfilled") setModels(m.value?.models || []);
      if (d.status  === "fulfilled") setDrift(d.value);
      setLoading(false);
    });
  }, []);

  const TABS = ["financial", "quality", "operations", "models"];

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "60vh", color: T.muted, fontFamily: T.mono }}>
      Loading admin dashboard…
    </div>
  );

  return (
    <div style={{ background: T.bg, minHeight: "100vh", fontFamily: T.mono, color: T.text, padding: "20px 28px" }}>

      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontFamily: T.disp, fontSize: 24, fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>
          Hospital Intelligence
        </h1>
        <div style={{ fontSize: 11, color: T.dim, marginTop: 4 }}>
          {cfo?.period || "April 2026"} · AI-Powered Administrative Intelligence
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 2, marginBottom: 20, background: T.surf, border: `1px solid ${T.border}`, borderRadius: 8, padding: 4 }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            flex: 1, padding: "9px", border: "none", borderRadius: 6, cursor: "pointer", fontFamily: T.mono, fontSize: 12, fontWeight: activeTab === t ? 600 : 400, textTransform: "capitalize", letterSpacing: "0.02em",
            background: activeTab === t ? "rgba(0,210,150,0.1)" : "transparent",
            color: activeTab === t ? T.accent : T.muted,
          }}>{t}</button>
        ))}
      </div>

      {/* ── FINANCIAL TAB ── */}
      {activeTab === "financial" && cfo && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>

          <Card title="Total Estimated Value MTD" colspan={2}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 12 }}>
              <span style={{ fontFamily: T.disp, fontSize: 48, fontWeight: 800, color: T.accent, letterSpacing: "-0.04em" }}>
                {fmt$(cfo.financial_impact.total_estimated_value_usd)}
              </span>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: T.accent }}>{cfo.financial_impact.roi_vs_subscription}× ROI</div>
                <div style={{ fontSize: 11, color: T.muted }}>vs subscription cost</div>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {[
                { label: "Readmission penalties avoided", value: cfo.financial_impact.readmission_penalty_avoided_usd, color: T.accent },
                { label: "LOS reduction savings",         value: cfo.financial_impact.los_savings_usd,                color: T.blue  },
                { label: "ADE prevention",                value: cfo.financial_impact.drug_adverse_event_prevention_usd, color: T.med },
                { label: "Documentation efficiency",      value: cfo.financial_impact.documentation_efficiency_usd,   color: T.muted },
              ].map(row => (
                <div key={row.label} style={{ padding: "10px 12px", background: "rgba(255,255,255,0.02)", border: `1px solid ${T.border}`, borderRadius: 7 }}>
                  <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 20, color: row.color }}>{fmt$(row.value)}</div>
                  <div style={{ fontSize: 10, color: T.dim, marginTop: 3, lineHeight: 1.4 }}>{row.label}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 10, fontSize: 10, color: T.dim }}>{cfo.financial_impact.methodology}</div>
          </Card>

          <Card title="Clinical Quality vs Benchmark">
            <KPI label="Readmission Rate"     value={fmtPct(cfo.clinical_quality.readmission_rate_pct / 100)} sub={`Benchmark: ${fmtPct(cfo.clinical_quality.national_benchmark_pct / 100)}`} color={T.accent} />
            <KPI label="Avg Length of Stay"   value={`${cfo.clinical_quality.avg_los_days}d`}       sub={`DRG benchmark: ${cfo.clinical_quality.drg_benchmark_los_days}d`} color={T.blue} />
            <KPI label="Sepsis Bundle SEP-1"  value={fmtPct(cfo.clinical_quality.sep1_bundle_compliance_pct / 100)} sub="Target: 90%"  color={T.accent} />
            <KPI label="Sepsis Mortality"     value={fmtPct(cfo.clinical_quality.sepsis_mortality_pct / 100)}       sub={`National: ${fmtPct(cfo.clinical_quality.national_sepsis_mortality_pct / 100)}`} color={T.med} />
          </Card>

          <Card title="AI Adoption">
            <KPI label="Recommendation acceptance" value={fmtPct(cfo.ai_adoption.recommendation_acceptance_rate)} color={T.accent} />
            <KPI label="Recommendations this month" value={cfo.ai_adoption.total_recommendations_this_month.toLocaleString()} />
            <div style={{ fontSize: 11, color: T.muted, marginTop: 8 }}>Active departments:</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              {cfo.ai_adoption.departments_using.map(d => (
                <span key={d} style={{ fontSize: 10, padding: "3px 8px", background: "rgba(0,210,150,0.08)", color: T.accent, borderRadius: 20, border: "1px solid rgba(0,210,150,0.2)" }}>{d}</span>
              ))}
            </div>
          </Card>

          <Card title="Bed Utilization">
            <KPI label="Current census"      value={`${cfo.operational.current_census} / ${cfo.operational.total_beds}`} color={T.text} />
            <KPI label="Occupancy rate"      value={fmtPct(cfo.operational.occupancy_rate)}     color={T.blue} />
            <KPI label="ICU occupancy"        value={fmtPct(cfo.operational.icu_occupancy_rate)} color={cfo.operational.icu_occupancy_rate > 0.90 ? T.high : T.text} />
            <div style={{ display: "flex", gap: 12, marginTop: 6 }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 20, color: T.accent }}>{cfo.operational.predicted_discharges_24h}</div>
                <div style={{ fontSize: 10, color: T.dim }}>predicted discharges 24h</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 20, color: T.high }}>{cfo.operational.predicted_admissions_24h}</div>
                <div style={{ fontSize: 10, color: T.dim }}>predicted admissions 24h</div>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* ── QUALITY TAB ── */}
      {activeTab === "quality" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>

          {quality && (
            <Card title="CMS Core Measures Compliance" colspan={2}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <span style={{ fontFamily: T.disp, fontSize: 20, fontWeight: 700, color: T.text }}>
                  Overall: <span style={{ color: quality.overall_compliance >= 0.90 ? T.accent : T.high }}>{fmtPct(quality.overall_compliance)}</span>
                </span>
                {quality.patients_at_deadline_risk > 0 && (
                  <span style={{ fontSize: 11, padding: "4px 12px", background: "rgba(244,197,66,0.1)", color: T.med, border: "1px solid rgba(244,197,66,0.25)", borderRadius: 20 }}>
                    ⚠ {quality.patients_at_deadline_risk} patients approaching deadlines
                  </span>
                )}
              </div>
              {quality.measures.map(m => (
                <QualityRow key={m.id} id={m.id} name={m.name} rate={m.compliance_rate}
                  benchmark={0.90} patients={m.patients_eligible} approaching={m.approaching_deadline} />
              ))}
            </Card>
          )}

          {readmit && (
            <Card title="CMS HRRP Readmission Rates vs National Average">
              {Object.entries(readmit.hrrp_conditions).map(([cond, data]) => (
                <HRRPRow key={cond} condition={cond} data={data} />
              ))}
              <div style={{ marginTop: 14, padding: "10px 12px", background: "rgba(0,210,150,0.05)", border: "1px solid rgba(0,210,150,0.15)", borderRadius: 7 }}>
                <div style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 16, color: T.accent }}>
                  Est. savings: {fmt$(readmit.estimated_savings)}
                </div>
                <div style={{ fontSize: 10, color: T.dim, marginTop: 3 }}>CMS penalties with AI vs without · {readmit.ai_attribution_note}</div>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ── OPERATIONS TAB ── */}
      {activeTab === "operations" && coo && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
          <Card title="Bed Utilization">
            {[
              { label: "Total beds",   value: coo.bed_utilization.total_beds,   color: T.text  },
              { label: "Occupied",     value: coo.bed_utilization.occupied,     color: T.high  },
              { label: "Available",    value: coo.bed_utilization.available,    color: T.accent},
              { label: "Cleaning",     value: coo.bed_utilization.cleaning,     color: T.muted },
            ].map(r => (
              <div key={r.label} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: `1px solid ${T.border}` }}>
                <span style={{ fontSize: 12, color: T.muted }}>{r.label}</span>
                <span style={{ fontFamily: T.disp, fontWeight: 700, fontSize: 18, color: r.color }}>{r.value}</span>
              </div>
            ))}
          </Card>
          <Card title="Staff Efficiency">
            <KPI label="Alert response time avg" value={`${coo.staff_efficiency.alert_response_time_avg_seconds}s`} color={coo.staff_efficiency.alert_response_time_avg_seconds < 120 ? T.accent : T.high} />
            <KPI label="Documentation hours saved MTD" value={coo.staff_efficiency.documentation_time_saved_hours_mtd} sub="hours" color={T.blue} />
            <KPI label="AI acceptance rate" value={fmtPct(coo.staff_efficiency.ai_acceptance_rate)} color={T.accent} />
          </Card>
          <Card title="Patient Flow">
            {[
              { label: "ED to bed avg",          value: `${coo.patient_flow.avg_ed_to_bed_hours}h`        },
              { label: "Avg discharge time",      value: coo.patient_flow.avg_discharge_time              },
              { label: "Discharge before noon",   value: fmtPct(coo.patient_flow.discharge_before_noon_pct) },
              { label: "Bed shortage predicted",  value: coo.patient_flow.predicted_bed_shortage ? "YES" : "No", color: coo.patient_flow.predicted_bed_shortage ? T.crit : T.accent },
            ].map(r => (
              <div key={r.label} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: `1px solid ${T.border}` }}>
                <span style={{ fontSize: 12, color: T.muted }}>{r.label}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: r.color || T.text }}>{r.value}</span>
              </div>
            ))}
          </Card>
        </div>
      )}

      {/* ── MODELS TAB ── */}
      {activeTab === "models" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          {models && (
            <Card title="AI Model Registry" colspan={2}>
              {models.map((m, i) => <ModelRow key={i} model={m} />)}
            </Card>
          )}
          {drift && (
            <Card title="Model Performance (Weekly Drift Monitor)">
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
                <span style={{ fontSize: 12, color: T.muted }}>Model: <strong style={{ color: T.text }}>{drift.model}</strong></span>
                <span style={{ fontSize: 12, color: T.muted }}>Baseline AUROC: <strong style={{ color: T.text }}>{drift.baseline_auroc}</strong></span>
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                    {["Week", "AUROC", "Acceptance", "Drift"].map(h => (
                      <th key={h} style={{ padding: "6px 8px", textAlign: h === "Week" ? "left" : "center", color: T.dim, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {drift.snapshots.map((s, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${T.border}` }}>
                      <td style={{ padding: "7px 8px", color: T.muted }}>{s.week}</td>
                      <td style={{ padding: "7px 8px", textAlign: "center", fontWeight: 700, color: s.auroc >= drift.baseline_auroc - 0.02 ? T.accent : T.high }}>{s.auroc}</td>
                      <td style={{ padding: "7px 8px", textAlign: "center", color: T.muted }}>{fmtPct(s.acceptance_rate)}</td>
                      <td style={{ padding: "7px 8px", textAlign: "center" }}>
                        <span style={{ fontSize: 10, padding: "2px 7px", background: s.drift_detected ? "rgba(255,61,90,0.1)" : "rgba(0,210,150,0.08)", color: s.drift_detected ? T.crit : T.accent, borderRadius: 4 }}>
                          {s.drift_detected ? "DRIFT" : "OK"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: 12, fontSize: 10, color: T.dim }}>Auto-updates frozen: <span style={{ color: drift.auto_updates_frozen ? T.crit : T.accent }}>{drift.auto_updates_frozen ? "YES — manual review required" : "No"}</span></div>
            </Card>
          )}
        </div>
      )}

      <div style={{ marginTop: 16, fontSize: 10, color: T.dim, textAlign: "center" }}>
        Financial estimates use CMS HRRP penalty rates × attribution modeling · Not for regulatory reporting without CFO review
      </div>
    </div>
  );
}
