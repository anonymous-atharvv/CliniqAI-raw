/**
 * CliniQAI WebSocket Service
 * Real-time ICU vitals and agent pipeline updates.
 *
 * WS_BASE reads from VITE_WS_URL:
 *   - Local:      ws://localhost:8000
 *   - Production: wss://your-app.up.railway.app
 */

const WS_BASE = import.meta.env.VITE_WS_URL || "ws://localhost:8000";

class Channel {
  constructor(url, onMsg, onErr, reconnectMs = 3000) {
    this._url = url;
    this._onMsg = onMsg;
    this._onErr = onErr || (() => {});
    this._reconnectMs = reconnectMs;
    this._ws = null;
    this._timer = null;
    this._closed = false;
    this._connect();
  }

  _connect() {
    if (this._closed) return;
    const token = localStorage.getItem("cq_access");
    const url = token ? `${this._url}?token=${token}` : this._url;
    this._ws = new WebSocket(url);
    this._ws.onopen    = () => console.debug(`[WS] connected ${this._url}`);
    this._ws.onmessage = (e) => { try { this._onMsg(JSON.parse(e.data)); } catch {} };
    this._ws.onerror   = (e) => this._onErr(e);
    this._ws.onclose   = (e) => {
      if (!this._closed && e.code !== 1000)
        this._timer = setTimeout(() => this._connect(), this._reconnectMs);
    };
  }

  send(data) {
    if (this._ws?.readyState === WebSocket.OPEN)
      this._ws.send(JSON.stringify(data));
  }

  close() {
    this._closed = true;
    clearTimeout(this._timer);
    this._ws?.close(1000, "unmounted");
  }

  get connected() { return this._ws?.readyState === WebSocket.OPEN; }
}

/** Subscribe to all patients in a ward (ICU board). Returns unsubscribe fn. */
export function subscribeToWard(wardId, onUpdate, onError) {
  const ch = new Channel(
    `${WS_BASE}/api/v1/vitals/ws/ward/${encodeURIComponent(wardId)}`,
    onUpdate, onError
  );
  return () => ch.close();
}

/** Subscribe to a single patient at 1Hz. Returns unsubscribe fn. */
export function subscribeToPatient(patientId, onUpdate, onError) {
  const ch = new Channel(
    `${WS_BASE}/api/v1/vitals/ws/patient/${patientId}`,
    onUpdate, onError, 2000
  );
  return () => ch.close();
}

/** Subscribe to agent pipeline progress. Returns unsubscribe fn. */
export function subscribeToAgents(patientId, onUpdate, onError) {
  const ch = new Channel(
    `${WS_BASE}/api/v1/agents/ws/sessions/${patientId}`,
    onUpdate, onError
  );
  return () => ch.close();
}

export default { subscribeToWard, subscribeToPatient, subscribeToAgents };
