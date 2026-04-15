import { useState, Suspense } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import ICUMonitor  from "./pages/ICUMonitor";
import PatientView from "./pages/PatientView";
import AdminPanel  from "./pages/AdminPanel";
import Compliance  from "./pages/Compliance";

const T = {
  bg:     "#050810",
  nav:    "#0c1120",
  border: "rgba(255,255,255,0.06)",
  accent: "#00d296",
  muted:  "#8e9bbf",
  dim:    "#4a5270",
  mono:   '"DM Mono", monospace',
  disp:   '"Syne", sans-serif',
};

const NAV_ITEMS = [
  { to: "/icu",        label: "ICU Monitor",   icon: "⚕" },
  { to: "/admin",      label: "Intelligence",  icon: "📊" },
  { to: "/compliance", label: "Compliance",    icon: "🛡" },
];

function Sidebar() {
  return (
    <nav style={{
      width: 200, flexShrink: 0, background: T.nav, borderRight: `1px solid ${T.border}`,
      display: "flex", flexDirection: "column", padding: "20px 0",
      position: "fixed", top: 0, left: 0, bottom: 0, zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{ padding: "0 20px 24px", borderBottom: `1px solid ${T.border}` }}>
        <div style={{ fontFamily: T.disp, fontSize: 20, fontWeight: 800, color: T.accent, letterSpacing: "-0.02em" }}>
          CliniQAI
        </div>
        <div style={{ fontSize: 9, color: T.dim, marginTop: 2, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Hospital Intelligence
        </div>
      </div>

      {/* Nav links */}
      <div style={{ flex: 1, padding: "16px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
        {NAV_ITEMS.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            style={({ isActive }) => ({
              display: "flex", alignItems: "center", gap: 10, padding: "9px 12px",
              borderRadius: 8, textDecoration: "none", fontSize: 12, fontFamily: T.mono, fontWeight: 500,
              background:  isActive ? "rgba(0,210,150,0.1)" : "transparent",
              color:       isActive ? T.accent : T.muted,
              border:      isActive ? "1px solid rgba(0,210,150,0.2)" : "1px solid transparent",
              transition:  "all 0.15s",
            })}
          >
            <span style={{ fontSize: 16 }}>{icon}</span>
            {label}
          </NavLink>
        ))}
      </div>

      {/* Footer */}
      <div style={{ padding: "16px 20px", borderTop: `1px solid ${T.border}` }}>
        <div style={{ fontSize: 9, color: T.dim, lineHeight: 1.5 }}>
          AI Decision Support Only<br />
          Physician review required
        </div>
        <div style={{ fontSize: 9, color: T.dim, marginTop: 6 }}>v1.0.0</div>
      </div>
    </nav>
  );
}

function Layout({ children }) {
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: T.bg }}>
      <Sidebar />
      <main style={{ flex: 1, marginLeft: 200, padding: "24px", overflowY: "auto" }}>
        {children}
      </main>
    </div>
  );
}

function LoadingFallback() {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "60vh", color: "#4a5270", fontFamily: T.mono, fontSize: 13 }}>
      ⚕ Loading…
    </div>
  );
}

export default function App() {
  const [selectedPatient, setSelectedPatient] = useState(null);

  return (
    <BrowserRouter>
      <Layout>
        <Suspense fallback={<LoadingFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/icu" replace />} />
            <Route
              path="/icu"
              element={
                selectedPatient
                  ? <PatientView patientId={selectedPatient.patient_deident_id || selectedPatient.bed_id} onBack={() => setSelectedPatient(null)} />
                  : <ICUMonitor wardCode="ICU-B" onSelectPatient={setSelectedPatient} />
              }
            />
            <Route path="/patient/:id" element={<PatientView onBack={() => window.history.back()} />} />
            <Route path="/admin"        element={<AdminPanel />} />
            <Route path="/compliance"   element={<Compliance />} />
            <Route path="*" element={<Navigate to="/icu" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </BrowserRouter>
  );
}
