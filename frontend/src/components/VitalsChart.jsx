/**
 * VitalsChart — live animated vital signs chart using Canvas.
 * Shows last 60 minutes, updates at 1Hz via WebSocket.
 */
import { useEffect, useRef, useState } from 'react';

const VITAL_COLORS = {
  heart_rate:       '#ff3d5a',
  spo2_pulse_ox:    '#f4c542',
  bp_systolic:      '#4d8fff',
  respiratory_rate: '#00d296',
  temperature:      '#c084fc',
};

const VITAL_RANGES = {
  heart_rate:       [30, 180],
  spo2_pulse_ox:    [80, 100],
  bp_systolic:      [60, 200],
  respiratory_rate: [5, 45],
  temperature:      [34, 42],
};

const VITAL_LABELS = {
  heart_rate: 'HR', spo2_pulse_ox: 'SpO₂', bp_systolic: 'SBP',
  respiratory_rate: 'RR', temperature: 'T°C',
};

export function VitalsChart({ vitalsHistory = {}, width = 500, height = 180, showLegend = true }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, width, height);
    const pad = { top: 10, bottom: 22, left: 6, right: 6 };
    const cW = width - pad.left - pad.right;
    const cH = height - pad.top - pad.bottom;

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (cH / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    }

    // Draw each vital line
    Object.entries(vitalsHistory).forEach(([param, readings]) => {
      if (!readings?.length) return;
      const color = VITAL_COLORS[param] || '#8e9bbf';
      const [yMin, yMax] = VITAL_RANGES[param] || [0, 200];
      const n = readings.length;

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.shadowColor = color;
      ctx.shadowBlur = 3;

      readings.forEach((v, i) => {
        const x = pad.left + (i / Math.max(n - 1, 1)) * cW;
        const y = pad.top + cH - ((Math.max(yMin, Math.min(yMax, v)) - yMin) / (yMax - yMin)) * cH;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0;
    });

    // Time axis labels
    ctx.fillStyle = 'rgba(142,155,191,0.6)';
    ctx.font = '10px "DM Mono", monospace';
    ctx.fillText('–60min', pad.left, height - 4);
    ctx.fillText('Now', width - pad.right - 24, height - 4);
  }, [vitalsHistory, width, height]);

  return (
    <div>
      <canvas ref={canvasRef} style={{ width, height, display: 'block' }} />
      {showLegend && (
        <div style={{ display: 'flex', gap: 12, marginTop: 6, flexWrap: 'wrap' }}>
          {Object.entries(VITAL_COLORS).map(([param, color]) => (
            <span key={param} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#8e9bbf' }}>
              <span style={{ width: 16, height: 2, background: color, display: 'inline-block', borderRadius: 2 }} />
              {VITAL_LABELS[param] || param}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default VitalsChart;
