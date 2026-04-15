/**
 * AIRecommendation — Displays structured LLM reasoning output.
 * Shows: patient summary, differentials, actions, confidence, data gaps.
 * Always shows the AI disclaimer. Always shows feedback buttons.
 */
import { RiskBadge } from './RiskBadge';
import { FeedbackButton } from './RiskBadge';

const URGENCY_STYLES = {
  immediate:  { bg: 'rgba(255,61,90,0.06)',  border: 'rgba(255,61,90,0.2)',  label: 'IMMEDIATE',  color: '#ff3d5a' },
  short_term: { bg: 'rgba(255,140,66,0.06)', border: 'rgba(255,140,66,0.2)', label: 'SHORT-TERM', color: '#ff8c42' },
  monitoring: { bg: 'rgba(255,255,255,0.02)', border: 'rgba(255,255,255,0.06)', label: 'MONITOR', color: '#8e9bbf' },
};

const CONFIDENCE_COLORS = { HIGH: '#00d296', MEDIUM: '#f4c542', LOW: '#ff8c42' };

const RANK_WIDTH = { primary: '100%', alternative: '65%', rule_out: '30%' };
const RANK_COLORS = { primary: '#ff3d5a', alternative: '#ff8c42', rule_out: '#4a5270' };

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#4a5270', fontWeight: 500, marginBottom: 8 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

export function AIRecommendation({ data, inferenceId, loading = false }) {
  if (loading) return (
    <div style={{ padding: 20, textAlign: 'center', color: '#8e9bbf', fontSize: 13 }}>
      <div style={{ marginBottom: 8 }}>⚕ Running AI clinical reasoning…</div>
      <div style={{ fontSize: 11, color: '#4a5270' }}>Typical time: 4–8 seconds</div>
    </div>
  );

  if (!data) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* Summary */}
      <Section title="Patient State">
        <p style={{ fontSize: 12, color: '#8e9bbf', lineHeight: 1.6, fontFamily: '"Instrument Serif", serif', fontStyle: 'italic' }}>
          "{data.patient_state_summary}"
        </p>
      </Section>

      {/* Risk */}
      <Section title="Risk Level">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <RiskBadge level={data.risk_level} size="md" />
          <span style={{ fontSize: 11, color: CONFIDENCE_COLORS[data.overall_confidence] }}>
            Confidence: {data.overall_confidence}
          </span>
        </div>
        {data.risk_justification && (
          <p style={{ fontSize: 11, color: '#8e9bbf', lineHeight: 1.5 }}>{data.risk_justification}</p>
        )}
      </Section>

      {/* Differentials */}
      {data.differential_diagnoses?.length > 0 && (
        <Section title="Differential Diagnoses">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.differential_diagnoses.map((dx, i) => (
              <div key={i} style={{ padding: '8px 10px', background: 'rgba(255,255,255,0.02)', border: `1px solid rgba(255,255,255,0.06)`, borderRadius: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <div style={{ width: RANK_WIDTH[dx.probability_rank] || '50%', height: 4, background: RANK_COLORS[dx.probability_rank] || '#4a5270', borderRadius: 2, maxWidth: 48, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#e8edf8', flex: 1 }}>{dx.condition}</span>
                  <span style={{ fontSize: 11, fontFamily: '"Syne", sans-serif', fontWeight: 700, color: RANK_COLORS[dx.probability_rank] }}>
                    {Math.round(dx.confidence * 100)}%
                  </span>
                </div>
                <div style={{ fontSize: 10, color: '#8e9bbf' }}>
                  {dx.icd10 && <span style={{ background: 'rgba(77,143,255,0.1)', color: '#4d8fff', padding: '1px 6px', borderRadius: 3, marginRight: 6, fontFamily: 'monospace' }}>{dx.icd10}</span>}
                  {dx.supporting_evidence?.slice(0, 2).join(' • ')}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Actions */}
      {data.recommended_actions?.length > 0 && (
        <Section title="Recommended Actions">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {data.recommended_actions.map((action, i) => {
              const s = URGENCY_STYLES[action.urgency] || URGENCY_STYLES.monitoring;
              return (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '7px 10px', background: s.bg, border: `1px solid ${s.border}`, borderRadius: 5 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: s.color, flexShrink: 0, letterSpacing: '0.04em', paddingTop: 1 }}>{s.label}</span>
                  <span style={{ fontSize: 11, color: '#8e9bbf', lineHeight: 1.4 }}>{action.action}</span>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Data Gaps */}
      {data.data_gaps?.length > 0 && (
        <Section title="Data Gaps Limiting Confidence">
          <div style={{ padding: '8px 10px', background: 'rgba(77,143,255,0.05)', border: '1px solid rgba(77,143,255,0.15)', borderRadius: 5 }}>
            <div style={{ fontSize: 11, color: '#8e9bbf', lineHeight: 1.6 }}>
              {data.data_gaps.join(' • ')}
            </div>
          </div>
        </Section>
      )}

      {/* Human review flag */}
      {data.human_review_required && (
        <div style={{ padding: '8px 10px', background: 'rgba(255,140,66,0.06)', border: '1px solid rgba(255,140,66,0.2)', borderRadius: 5, marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: '#ff8c42', fontWeight: 600 }}>⚠ Physician review required</div>
          <div style={{ fontSize: 11, color: '#8e9bbf', marginTop: 2 }}>{data.human_review_reason}</div>
        </div>
      )}

      {/* Disclaimer */}
      <div style={{ padding: '7px 10px', background: 'rgba(77,143,255,0.04)', border: '1px solid rgba(77,143,255,0.12)', borderRadius: 5, marginBottom: 10 }}>
        <div style={{ fontSize: 10, color: '#4a5270', lineHeight: 1.5 }}>
          <strong style={{ color: '#4d8fff' }}>AI Decision Support Only.</strong> All recommendations require physician review before clinical action. Confidence values are probabilistic.
        </div>
      </div>

      {/* Feedback */}
      {inferenceId && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'rgba(255,255,255,0.02)', borderRadius: 5, border: '1px solid rgba(255,255,255,0.05)' }}>
          <span style={{ fontSize: 11, color: '#8e9bbf', flex: 1 }}>Was this assessment helpful?</span>
          <FeedbackButton inferenceId={inferenceId} compact />
        </div>
      )}
    </div>
  );
}

export default AIRecommendation;
