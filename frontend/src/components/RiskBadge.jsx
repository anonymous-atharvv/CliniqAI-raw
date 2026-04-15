/**
 * RiskBadge — displays patient risk level with appropriate visual weight.
 * CRITICAL pulses. HIGH glows amber. MEDIUM amber. LOW green.
 */
export function RiskBadge({ level, size = 'md', showDot = true }) {
  const config = {
    CRITICAL: { bg: 'rgba(255,61,90,0.12)',  border: 'rgba(255,61,90,0.35)',  text: '#ff3d5a', pulse: true  },
    HIGH:     { bg: 'rgba(255,140,66,0.10)', border: 'rgba(255,140,66,0.30)', text: '#ff8c42', pulse: false },
    MEDIUM:   { bg: 'rgba(244,197,66,0.10)', border: 'rgba(244,197,66,0.30)', text: '#f4c542', pulse: false },
    LOW:      { bg: 'rgba(0,210,150,0.08)',  border: 'rgba(0,210,150,0.25)',  text: '#00d296', pulse: false },
  };
  const c = config[level] || config.LOW;
  const sizes = { sm: '10px', md: '12px', lg: '14px' };
  const pads  = { sm: '2px 7px', md: '3px 10px', lg: '4px 13px' };

  const style = {
    display: 'inline-flex', alignItems: 'center', gap: '5px',
    padding: pads[size], borderRadius: '20px',
    background: c.bg, border: `1px solid ${c.border}`, color: c.text,
    fontSize: sizes[size], fontWeight: 700, letterSpacing: '0.03em',
    fontFamily: '"Syne", sans-serif',
    animation: c.pulse ? 'badge-pulse 2s ease infinite' : 'none',
  };

  return (
    <span style={style}>
      {showDot && <span style={{ width: 5, height: 5, borderRadius: '50%', background: c.text, display: 'inline-block', flexShrink: 0 }} />}
      {level}
    </span>
  );
}

---

/**
 * FeedbackButton — 1-tap physician feedback on AI recommendations.
 * CRITICAL UX rule: must add <3 seconds to physician workflow.
 * Thumbs up = accepted, thumbs down = rejected.
 * Optional text reason opens on thumbs-down.
 */
import { useState } from 'react';
import { inference } from '../services/api';

export function FeedbackButton({ inferenceId, compact = false, onFeedback }) {
  const [state, setState] = useState('idle');     // idle | up | down | submitting | done
  const [showReason, setShowReason] = useState(false);
  const [reason, setReason] = useState('');

  async function submit(signal, isPositive, reasonText = '') {
    setState('submitting');
    try {
      await inference.submitFeedback(inferenceId, signal, isPositive, reasonText);
      setState(isPositive ? 'up' : 'down');
      onFeedback?.({ signal, isPositive });
      setTimeout(() => setState('idle'), 3000);
    } catch {
      setState('idle');
    }
  }

  if (state === 'done' || state === 'up' || state === 'down') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#8e9bbf' }}>
        <span>{state === 'up' ? '👍' : '👎'}</span>
        <span>Feedback recorded</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {!compact && <span style={{ fontSize: 11, color: '#8e9bbf' }}>Was this helpful?</span>}
        <button
          onClick={() => submit('thumbs_up', true)}
          disabled={state === 'submitting'}
          style={{ width: 30, height: 30, borderRadius: 4, border: '1px solid rgba(0,210,150,0.25)', background: 'transparent', cursor: 'pointer', fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s' }}
          title="Helpful"
        >👍</button>
        <button
          onClick={() => { setState('down'); setShowReason(true); }}
          disabled={state === 'submitting'}
          style={{ width: 30, height: 30, borderRadius: 4, border: '1px solid rgba(255,61,90,0.25)', background: 'transparent', cursor: 'pointer', fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.15s' }}
          title="Not helpful"
        >👎</button>
      </div>
      {showReason && (
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            type="text"
            placeholder="Optional: reason (voice or type)"
            value={reason}
            onChange={e => setReason(e.target.value)}
            style={{ flex: 1, fontSize: 11, padding: '4px 8px', background: 'var(--bg-elevated, #0f1524)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 4, color: '#e8edf8' }}
          />
          <button onClick={() => submit('thumbs_down', false, reason)} style={{ fontSize: 11, padding: '4px 10px', background: 'rgba(255,61,90,0.15)', border: '1px solid rgba(255,61,90,0.3)', borderRadius: 4, color: '#ff3d5a', cursor: 'pointer' }}>
            Submit
          </button>
        </div>
      )}
    </div>
  );
}

---

/**
 * AgentStatus — real-time display of the 7 AI agent pipeline.
 */
import { useState, useEffect } from 'react';
import { agents as agentsApi } from '../services/api';

const AGENT_LABELS = {
  triage_agent:        'Triage',
  risk_agent:          'Risk',
  diagnosis_agent:     'Diagnosis',
  pharmacist_agent:    'Pharmacist',
  documentation_agent: 'Documentation',
  coordinator_agent:   'Coordinator',
  escalation_agent:    'Escalation',
};

export function AgentStatus({ compact = false }) {
  const [agentData, setAgentData] = useState([]);

  useEffect(() => {
    agentsApi.getAllStatus().then(setAgentData).catch(() => {});
    const id = setInterval(() => agentsApi.getAllStatus().then(setAgentData).catch(() => {}), 15000);
    return () => clearInterval(id);
  }, []);

  const dot = (status) => {
    const colors = { running: '#00d296', idle: '#4a5270', completed: '#00d296', failed: '#ff3d5a', timeout: '#ff8c42', circuit_open: '#ff3d5a' };
    return (
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: colors[status] || '#4a5270', display: 'inline-block', flexShrink: 0, animation: status === 'running' ? 'pulse 1.5s ease infinite' : 'none' }} />
    );
  };

  if (compact) return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {agentData.map(a => (
        <span key={a.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#8e9bbf' }}>
          {dot(a.status)} {AGENT_LABELS[a.agent_id] || a.agent_id}
        </span>
      ))}
    </div>
  );

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
      {agentData.map(a => (
        <div key={a.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 6, border: '1px solid rgba(255,255,255,0.06)' }}>
          {dot(a.status)}
          <span style={{ flex: 1, fontSize: 11, color: '#e8edf8', fontWeight: 500 }}>{AGENT_LABELS[a.agent_id]}</span>
          <span style={{ fontSize: 10, color: '#4a5270' }}>{a.avg_latency_ms}ms</span>
        </div>
      ))}
    </div>
  );
}
