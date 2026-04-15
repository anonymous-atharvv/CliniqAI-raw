/**
 * PatientCard — compact patient summary with risk level and key vitals.
 * Used in patient list and ward overview.
 */
import { RiskBadge } from './RiskBadge';

export function PatientCard({ patient, onClick, selected = false }) {
  const cardStyle = {
    background: selected ? 'rgba(0,210,150,0.06)' : 'rgba(255,255,255,0.02)',
    border: `1px solid ${selected ? 'rgba(0,210,150,0.25)' : 'rgba(255,255,255,0.06)'}`,
    borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
    transition: 'all 0.15s', display: 'flex', flexDirection: 'column', gap: 8,
  };

  const vitals = patient.latest_vitals || {};
  const critical = (param, val) => {
    const crit = { heart_rate: [40, 120], spo2_pulse_ox: [90, 101], bp_systolic: [80, 200] };
    const [lo, hi] = crit[param] || [0, 999];
    return val < lo || val >= hi;
  };

  return (
    <div style={cardStyle} onClick={() => onClick?.(patient)}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <span style={{ fontFamily: '"Syne", sans-serif', fontWeight: 700, fontSize: 14, color: '#e8edf8' }}>
            {patient.bed_id || patient.patient_id?.slice(0, 8)}
          </span>
          <span style={{ fontSize: 11, color: '#8e9bbf', marginLeft: 8 }}>{patient.patient_summary || ''}</span>
        </div>
        <RiskBadge level={patient.risk_level || 'LOW'} size="sm" />
      </div>

      {Object.keys(vitals).length > 0 && (
        <div style={{ display: 'flex', gap: 10 }}>
          {Object.entries(vitals).slice(0, 5).map(([param, val]) => {
            const isCrit = critical(param, val);
            const labels = { heart_rate: 'HR', spo2_pulse_ox: 'SpO₂', bp_systolic: 'SBP', respiratory_rate: 'RR', temperature: 'T°' };
            return (
              <div key={param} style={{ textAlign: 'center', minWidth: 32 }}>
                <div style={{ fontFamily: '"Syne", sans-serif', fontSize: 13, fontWeight: 700, color: isCrit ? '#ff3d5a' : '#e8edf8' }}>
                  {typeof val === 'number' ? val.toFixed(param === 'temperature' ? 1 : 0) : val}
                </div>
                <div style={{ fontSize: 9, color: '#4a5270', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {labels[param] || param}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {patient.news2_score !== undefined && (
        <div style={{ display: 'flex', gap: 8, fontSize: 11 }}>
          <span style={{ color: '#8e9bbf' }}>NEWS2</span>
          <span style={{ fontWeight: 700, color: patient.news2_score >= 5 ? '#ff8c42' : '#00d296' }}>
            {patient.news2_score}
          </span>
          {patient.sepsis_probability_12h !== undefined && (
            <>
              <span style={{ color: '#4a5270' }}>•</span>
              <span style={{ color: '#8e9bbf' }}>Sepsis</span>
              <span style={{ fontWeight: 700, color: patient.sepsis_probability_12h > 0.5 ? '#ff3d5a' : '#8e9bbf' }}>
                {(patient.sepsis_probability_12h * 100).toFixed(0)}%
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default PatientCard;
