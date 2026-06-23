#!/usr/bin/env python3
"""
PSBA AICC Web Dashboard
- Service status + uptime for Sara and Saima
- Last 5 call transcripts
- Live call log with transcripts
- Restart / Stop controls
- Runs on port 8080
"""

import re
import subprocess
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

SERVICES = {
    "sara":  "aiagent",
    "saima": "saima",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), -1


def get_service_info(svc):
    active, _ = _run(["systemctl", "is-active", svc])
    ts_raw, _ = _run(["systemctl", "show", svc, "--property=ActiveEnterTimestamp", "--value"])
    uptime = ""
    if active == "active" and ts_raw:
        try:
            dt = datetime.strptime(ts_raw, "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            diff = datetime.now(timezone.utc) - dt
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m = rem // 60
            uptime = f"{h}h {m}m" if h else f"{m}m"
        except Exception:
            uptime = ""
    return {"status": active, "uptime": uptime}


LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+\[(\w+)\]\s+(?:\[[^\]]*\]\s+)?(.+)$"
)
TS_START = re.compile(r"^\d{4}-\d{2}-\d{2}")

def _merge_lines(raw):
    """Join continuation lines (e.g. UUID bracket split across lines) back to their parent."""
    merged = []
    for line in raw.splitlines():
        if TS_START.match(line):
            merged.append(line)
        elif merged:
            merged[-1] = merged[-1].rstrip() + " " + line.strip()
    return merged

def _classify(msg, svc):
    if msg.startswith("USER:"):
        return "user", msg[5:].strip()
    if msg.startswith("AGENT:") or msg.startswith("SARA:"):
        return "sara", msg.split(":", 1)[1].strip()
    if msg.startswith("SAIMA:"):
        return "saima", msg[6:].strip()
    if "Call started" in msg:
        return "call_start", "Call started"
    if "Call ended" in msg or "Caller disconnected" in msg:
        return "call_end", "Call ended"
    if msg.startswith("Barge-in"):
        return "bargein", msg
    if msg.startswith("Connection from"):
        return "connection", msg
    return "info", msg


def get_logs(lines=150):
    entries = []
    for agent_label, svc in [("Sara", "aiagent"), ("Saima", "saima")]:
        raw, _ = _run([
            "journalctl", "-u", svc, "--output=cat",
            "--no-pager", "-n", str(lines), "--since", "today"
        ])
        for line in _merge_lines(raw):
            m = LINE_RE.match(line)
            if not m:
                continue
            ts, level, msg = m.groups()
            if level == "ERROR" or "error" in msg.lower():
                evt, text = "error", msg
            else:
                evt, text = _classify(msg, svc)
            if evt == "info" and ("HTTP Request" in msg or "KeepAlive" in msg or
                                   "Deepgram STT connected" in msg):
                continue
            entries.append({
                "time": ts[11:],
                "agent": agent_label,
                "event": evt,
                "message": text,
            })
    entries.sort(key=lambda x: x["time"])
    return entries[-200:]


def get_call_counts():
    counts = {}
    for label, svc in [("sara", "aiagent"), ("saima", "saima")]:
        raw, _ = _run([
            "journalctl", "-u", svc, "--output=cat",
            "--no-pager", "--since", "today"
        ], timeout=8)
        counts[label] = raw.count("Call started")
    counts["total"] = counts["sara"] + counts["saima"]
    return counts


def get_recent_transcripts(n=5):
    """Parse all today's logs and return the last n completed calls with conversation turns."""
    all_events = []
    for agent_label, svc in [("Sara", "aiagent"), ("Saima", "saima")]:
        raw, _ = _run([
            "journalctl", "-u", svc, "--output=cat",
            "--no-pager", "--since", "today"
        ], timeout=10)
        for line in _merge_lines(raw):
            m = LINE_RE.match(line)
            if not m:
                continue
            ts, level, msg = m.groups()
            evt, text = _classify(msg, svc)
            all_events.append({
                "ts": ts,          # full "YYYY-MM-DD HH:MM:SS"
                "time": ts[11:],   # "HH:MM:SS"
                "agent": agent_label,
                "event": evt,
                "message": text,
            })

    all_events.sort(key=lambda x: x["ts"])

    # Group into calls
    calls = []
    current = None
    for e in all_events:
        if e["event"] == "call_start":
            current = {
                "agent": e["agent"],
                "start": e["time"],
                "end": None,
                "duration": None,
                "turns": [],
            }
        elif current is not None:
            if e["event"] == "call_end":
                current["end"] = e["time"]
                # Compute duration
                try:
                    t0 = datetime.strptime(current["start"], "%H:%M:%S")
                    t1 = datetime.strptime(e["time"], "%H:%M:%S")
                    secs = int((t1 - t0).total_seconds())
                    current["duration"] = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
                except Exception:
                    current["duration"] = "—"
                calls.append(current)
                current = None
            elif e["event"] in ("user", "sara", "saima"):
                current["turns"].append({
                    "role": "user" if e["event"] == "user" else "agent",
                    "text": e["message"],
                    "time": e["time"],
                })

    # Include in-progress call at top if one is open
    if current and current["turns"]:
        current["end"] = None
        current["duration"] = None
        calls.append(current)

    # Return last n, newest first
    return list(reversed(calls))[:n]


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify({
        "sara":  get_service_info("aiagent"),
        "saima": get_service_info("saima"),
        "time":  datetime.now().strftime("%H:%M:%S"),
    })


@app.route("/api/logs")
def api_logs():
    return jsonify({"entries": get_logs()})


@app.route("/api/calls")
def api_calls():
    return jsonify(get_call_counts())


@app.route("/api/transcripts")
def api_transcripts():
    return jsonify({"calls": get_recent_transcripts()})


@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.get_json() or {}
    service = data.get("service", "")
    action  = data.get("action", "")
    if service not in {"aiagent", "saima"} or action not in {"restart", "stop", "start"}:
        return jsonify({"ok": False, "error": "invalid request"}), 400
    _, code = _run(["sudo", "/usr/bin/systemctl", action, service], timeout=15)
    return jsonify({"ok": code == 0})


# ── Dashboard HTML ────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PSBA AICC Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; }

  header { background: #1e293b; border-bottom: 1px solid #334155;
           padding: 1rem 2rem; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 1.2rem; font-weight: 600; color: #f1f5f9; letter-spacing: 0.02em; }
  header h1 span { color: #38bdf8; }
  #last-updated { font-size: 0.75rem; color: #64748b; }

  main { padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }

  /* ── Agent Cards ── */
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }
  .card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
  .card-title { font-size: 1rem; font-weight: 600; color: #f1f5f9; }
  .card-sub { font-size: 0.75rem; color: #64748b; margin-top: 0.1rem; }

  .status-badge { display: flex; align-items: center; gap: 0.4rem;
                  font-size: 0.75rem; font-weight: 600; }
  .dot { width: 8px; height: 8px; border-radius: 50%; }
  .dot.active { background: #22c55e; box-shadow: 0 0 0 3px #22c55e33;
                animation: pulse 2s infinite; }
  .dot.inactive { background: #ef4444; }
  .dot.failed   { background: #f97316; }
  @keyframes pulse { 0%,100% { box-shadow: 0 0 0 3px #22c55e33; }
                     50%      { box-shadow: 0 0 0 6px #22c55e11; } }

  .stats { display: flex; gap: 2rem; margin-bottom: 1rem; }
  .stat-label { font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-value { font-size: 1.4rem; font-weight: 700; color: #f1f5f9; }

  .controls { display: flex; gap: 0.5rem; }
  button { border: none; border-radius: 0.375rem; padding: 0.4rem 0.9rem;
           font-size: 0.8rem; font-weight: 500; cursor: pointer; transition: opacity 0.15s; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-restart { background: #3b82f6; color: #fff; }
  .btn-restart:hover:not(:disabled) { background: #2563eb; }
  .btn-stop    { background: #374151; color: #d1d5db; }
  .btn-stop:hover:not(:disabled) { background: #4b5563; }
  .btn-start   { background: #16a34a; color: #fff; }
  .btn-start:hover:not(:disabled) { background: #15803d; }

  /* ── Section header row ── */
  .section-row { display: flex; align-items: center; justify-content: space-between;
                 margin-bottom: 0.75rem; }
  .section-row h2 { font-size: 0.9rem; font-weight: 600; color: #f1f5f9; }
  .section-meta { font-size: 0.75rem; color: #64748b; }

  /* ── Transcript Section ── */
  .transcripts-wrap { margin-bottom: 1.5rem; }
  .tx-list { display: flex; flex-direction: column; gap: 0.75rem; }

  .tx-card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem;
             overflow: hidden; }
  .tx-card-header { display: flex; align-items: center; justify-content: space-between;
                    padding: 0.75rem 1.1rem; cursor: pointer; user-select: none;
                    transition: background 0.15s; }
  .tx-card-header:hover { background: #243044; }
  .tx-card-header.open  { border-bottom: 1px solid #334155; }

  .tx-meta { display: flex; align-items: center; gap: 0.75rem; }
  .tx-agent-badge { font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0.55rem;
                    border-radius: 0.25rem; }
  .tx-agent-badge.sara  { background: #2e1a5e; color: #c4b5fd; }
  .tx-agent-badge.saima { background: #4a1942; color: #f9a8d4; }
  .tx-time  { font-size: 0.75rem; color: #94a3b8; font-family: monospace; }
  .tx-dur   { font-size: 0.72rem; color: #64748b; }
  .tx-turns-count { font-size: 0.72rem; color: #64748b; }

  .tx-chevron { font-size: 0.65rem; color: #475569; transition: transform 0.2s; }
  .tx-chevron.open { transform: rotate(180deg); }

  .tx-live-badge { font-size: 0.65rem; font-weight: 700; background: #14532d;
                   color: #86efac; padding: 0.1rem 0.45rem; border-radius: 0.25rem;
                   animation: pulse 2s infinite; }

  .tx-body { padding: 0.85rem 1.1rem; display: none; }
  .tx-body.open { display: block; }

  .tx-empty { font-size: 0.78rem; color: #475569; font-style: italic; }

  /* Chat bubbles */
  .chat { display: flex; flex-direction: column; gap: 0.5rem; }
  .bubble-row { display: flex; gap: 0.5rem; align-items: flex-end; }
  .bubble-row.user  { justify-content: flex-start; }
  .bubble-row.agent { justify-content: flex-end; }

  .bubble { max-width: 75%; padding: 0.45rem 0.75rem; border-radius: 0.75rem;
            font-size: 0.8rem; line-height: 1.45; word-break: break-word; }
  .bubble.user  { background: #1e3a5f; color: #bfdbfe; border-bottom-left-radius: 0.2rem; }
  .bubble.agent-sara  { background: #2e1a5e; color: #ddd6fe; border-bottom-right-radius: 0.2rem; }
  .bubble.agent-saima { background: #4a1942; color: #fce7f3; border-bottom-right-radius: 0.2rem; }

  .bubble-label { font-size: 0.62rem; color: #475569; white-space: nowrap; margin-bottom: 0.1rem; }
  .bubble-row.user  .bubble-label { text-align: left; }
  .bubble-row.agent .bubble-label { text-align: right; }

  .bubble-col { display: flex; flex-direction: column; }
  .bubble-row.agent .bubble-col { align-items: flex-end; }

  /* ── Log Section ── */
  .log-section { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; overflow: hidden; }
  .log-header { padding: 0.9rem 1.25rem; border-bottom: 1px solid #334155;
                display: flex; align-items: center; justify-content: space-between; }
  .log-header h2 { font-size: 0.9rem; font-weight: 600; color: #f1f5f9; }
  .log-count { font-size: 0.75rem; color: #64748b; }

  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th { background: #162032; color: #64748b; font-weight: 500; text-align: left;
       padding: 0.5rem 1rem; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td { padding: 0.45rem 1rem; border-top: 1px solid #1e293b; vertical-align: top; }
  tr:nth-child(even) td { background: #162032; }
  tr:hover td { background: #1a2844; }

  .t-time  { color: #64748b; white-space: nowrap; width: 80px; font-family: monospace; }
  .t-agent { white-space: nowrap; width: 60px; font-weight: 600; }
  .t-event { white-space: nowrap; width: 90px; }
  .t-msg   { word-break: break-word; }

  .badge { display: inline-block; border-radius: 0.25rem; padding: 0.1rem 0.4rem;
           font-size: 0.68rem; font-weight: 600; }

  .ev-user       { color: #60a5fa; } .bg-user       { background: #1e3a5f; color: #93c5fd; }
  .ev-sara       { color: #a78bfa; } .bg-sara       { background: #2e1a5e; color: #c4b5fd; }
  .ev-saima      { color: #f472b6; } .bg-saima      { background: #4a1942; color: #f9a8d4; }
  .ev-call_start { color: #22c55e; } .bg-call_start { background: #14532d; color: #86efac; }
  .ev-call_end   { color: #94a3b8; } .bg-call_end   { background: #1e293b; color: #94a3b8; }
  .ev-bargein    { color: #fbbf24; } .bg-bargein    { background: #451a03; color: #fde68a; }
  .ev-error      { color: #f87171; } .bg-error      { background: #450a0a; color: #fca5a5; }
  .ev-connection { color: #475569; } .bg-connection { background: #1e293b; color: #64748b; }
  .ev-info       { color: #475569; } .bg-info       { background: #1e293b; color: #64748b; }

  .agent-sara  { color: #a78bfa; }
  .agent-saima { color: #f472b6; }

  .filter-bar { display: flex; gap: 0.5rem; align-items: center; }
  .filter-btn { background: #334155; color: #94a3b8; border-radius: 0.375rem;
                padding: 0.25rem 0.7rem; font-size: 0.72rem; cursor: pointer;
                border: 1px solid #475569; transition: all 0.15s; }
  .filter-btn.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }

  @media (max-width: 700px) { .cards { grid-template-columns: 1fr; }
    main { padding: 1rem; } }
</style>
</head>
<body>

<header>
  <div>
    <h1>PSBA <span>AICC</span> Dashboard</h1>
  </div>
  <span id="last-updated">Loading…</span>
</header>

<main>
  <!-- Agent Status Cards -->
  <div class="cards">
    <!-- Sara -->
    <div class="card" id="card-sara">
      <div class="card-header">
        <div>
          <div class="card-title">Sara &nbsp;·&nbsp; English</div>
          <div class="card-sub">Ext 9000 &nbsp;·&nbsp; aiagent.service</div>
        </div>
        <div class="status-badge" id="badge-sara">
          <div class="dot"></div> <span>—</span>
        </div>
      </div>
      <div class="stats">
        <div><div class="stat-label">Uptime</div><div class="stat-value" id="uptime-sara">—</div></div>
        <div><div class="stat-label">Calls Today</div><div class="stat-value" id="calls-sara">—</div></div>
      </div>
      <div class="controls">
        <button class="btn-restart" onclick="control('aiagent','restart',this)">Restart</button>
        <button class="btn-stop"    onclick="control('aiagent','stop',this)"   id="stop-sara">Stop</button>
        <button class="btn-start"   onclick="control('aiagent','start',this)"  id="start-sara" style="display:none">Start</button>
      </div>
    </div>

    <!-- Saima -->
    <div class="card" id="card-saima">
      <div class="card-header">
        <div>
          <div class="card-title">Saima &nbsp;·&nbsp; Urdu</div>
          <div class="card-sub">Ext 8000 &nbsp;·&nbsp; saima.service</div>
        </div>
        <div class="status-badge" id="badge-saima">
          <div class="dot"></div> <span>—</span>
        </div>
      </div>
      <div class="stats">
        <div><div class="stat-label">Uptime</div><div class="stat-value" id="uptime-saima">—</div></div>
        <div><div class="stat-label">Calls Today</div><div class="stat-value" id="calls-saima">—</div></div>
      </div>
      <div class="controls">
        <button class="btn-restart" onclick="control('saima','restart',this)">Restart</button>
        <button class="btn-stop"    onclick="control('saima','stop',this)"   id="stop-saima">Stop</button>
        <button class="btn-start"   onclick="control('saima','start',this)"  id="start-saima" style="display:none">Start</button>
      </div>
    </div>
  </div>

  <!-- Recent Call Transcripts -->
  <div class="transcripts-wrap">
    <div class="section-row">
      <h2>Recent Call Transcripts</h2>
      <span class="section-meta" id="tx-meta">Last 5 calls today</span>
    </div>
    <div class="tx-list" id="tx-list">
      <div style="color:#475569;font-size:0.8rem;padding:1rem 0;">Loading…</div>
    </div>
  </div>

  <!-- Live Call Log -->
  <div class="log-section">
    <div class="log-header">
      <h2>Live Call Log</h2>
      <div style="display:flex;align-items:center;gap:1rem;">
        <div class="filter-bar">
          <button class="filter-btn active" onclick="setFilter('all',this)">All</button>
          <button class="filter-btn" onclick="setFilter('sara',this)">Sara</button>
          <button class="filter-btn" onclick="setFilter('saima',this)">Saima</button>
          <button class="filter-btn" onclick="setFilter('calls',this)">Calls only</button>
        </div>
        <span class="log-count" id="log-count">—</span>
      </div>
    </div>
    <div style="overflow-x:auto;max-height:520px;overflow-y:auto;">
      <table>
        <thead>
          <tr>
            <th class="t-time">Time</th>
            <th class="t-agent">Agent</th>
            <th class="t-event">Event</th>
            <th class="t-msg">Message</th>
          </tr>
        </thead>
        <tbody id="log-body">
          <tr><td colspan="4" style="text-align:center;color:#475569;padding:2rem;">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<script>
const EVENT_LABELS = {
  user: 'USER', sara: 'SARA', saima: 'SAIMA',
  call_start: 'CALL IN', call_end: 'CALL END',
  bargein: 'BARGE-IN', error: 'ERROR',
  connection: 'CONNECT', info: 'INFO'
};

let currentFilter = 'all';
let allEntries = [];

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Transcripts ──────────────────────────────────────────────────────────────

function renderTranscripts(calls) {
  const list = document.getElementById('tx-list');
  const meta = document.getElementById('tx-meta');

  if (!calls.length) {
    list.innerHTML = '<div style="color:#475569;font-size:0.8rem;padding:0.5rem 0;">No calls recorded today yet.</div>';
    meta.textContent = 'No calls today';
    return;
  }

  meta.textContent = `Last ${calls.length} call${calls.length > 1 ? 's' : ''} today`;

  list.innerHTML = calls.map((call, idx) => {
    const agentKey  = call.agent.toLowerCase();
    const isLive    = call.end === null;
    const durText   = isLive ? '' : (call.duration || '—');
    const turnCount = call.turns.length;

    const bubbles = call.turns.map(t => {
      const isUser   = t.role === 'user';
      const rowClass = isUser ? 'user' : 'agent';
      const bubClass = isUser ? 'user' : `agent-${agentKey}`;
      const labelTxt = isUser ? 'Caller' : call.agent;
      return `<div class="bubble-row ${rowClass}">
        <div class="bubble-col">
          <div class="bubble-label">${esc(labelTxt)}</div>
          <div class="bubble ${bubClass}">${esc(t.text)}</div>
        </div>
      </div>`;
    }).join('');

    const bodyHtml = turnCount
      ? `<div class="chat">${bubbles}</div>`
      : `<div class="tx-empty">No transcript lines captured for this call.</div>`;

    // First call starts open
    const open = idx === 0;

    return `<div class="tx-card">
      <div class="tx-card-header ${open ? 'open' : ''}" onclick="toggleTx(this)">
        <div class="tx-meta">
          <span class="tx-agent-badge ${agentKey}">${esc(call.agent)}</span>
          <span class="tx-time">${esc(call.start)}${call.end ? ' – ' + esc(call.end) : ''}</span>
          ${durText ? `<span class="tx-dur">${esc(durText)}</span>` : ''}
          ${isLive ? '<span class="tx-live-badge">LIVE</span>' : ''}
          <span class="tx-turns-count">${turnCount} turn${turnCount !== 1 ? 's' : ''}</span>
        </div>
        <span class="tx-chevron ${open ? 'open' : ''}">▼</span>
      </div>
      <div class="tx-body ${open ? 'open' : ''}">${bodyHtml}</div>
    </div>`;
  }).join('');
}

function toggleTx(header) {
  const body    = header.nextElementSibling;
  const chevron = header.querySelector('.tx-chevron');
  const isOpen  = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  header.classList.toggle('open', !isOpen);
  chevron.classList.toggle('open', !isOpen);
}

// ── Log ──────────────────────────────────────────────────────────────────────

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderLog(allEntries);
}

function renderLog(entries) {
  allEntries = entries;
  let filtered = entries;
  if (currentFilter === 'sara')  filtered = entries.filter(e => e.agent === 'Sara');
  if (currentFilter === 'saima') filtered = entries.filter(e => e.agent === 'Saima');
  if (currentFilter === 'calls') filtered = entries.filter(e =>
    ['call_start','call_end','user','sara','saima'].includes(e.event));

  const tbody = document.getElementById('log-body');
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#475569;padding:2rem;">No events yet today</td></tr>';
    document.getElementById('log-count').textContent = '0 events';
    return;
  }

  const rows = filtered.slice().reverse().map(e => {
    const agentClass = e.agent === 'Sara' ? 'agent-sara' : 'agent-saima';
    const label = EVENT_LABELS[e.event] || e.event.toUpperCase();
    const msg = e.event === 'error'
      ? `<span style="color:#f87171">${esc(e.message)}</span>`
      : esc(e.message);
    return `<tr>
      <td class="t-time">${esc(e.time)}</td>
      <td class="t-agent ${agentClass}">${esc(e.agent)}</td>
      <td class="t-event"><span class="badge bg-${e.event}">${label}</span></td>
      <td class="t-msg">${msg}</td>
    </tr>`;
  }).join('');

  tbody.innerHTML = rows;
  document.getElementById('log-count').textContent = filtered.length + ' events';
}

// ── Status badges ─────────────────────────────────────────────────────────────

function updateBadge(id, status, uptime) {
  const badge = document.getElementById('badge-' + id);
  const dot   = badge.querySelector('.dot');
  const label = badge.querySelector('span');
  dot.className = 'dot ' + (status === 'active' ? 'active' : status === 'failed' ? 'failed' : 'inactive');
  label.textContent = status === 'active' ? ('Active' + (uptime ? ' · ' + uptime : '')) : status;

  const stopBtn  = document.getElementById('stop-'  + id);
  const startBtn = document.getElementById('start-' + id);
  if (status === 'active') {
    stopBtn.style.display  = '';
    startBtn.style.display = 'none';
  } else {
    stopBtn.style.display  = 'none';
    startBtn.style.display = '';
  }
}

// ── Refresh ───────────────────────────────────────────────────────────────────

function refresh() {
  Promise.all([
    fetch('/api/status').then(r => r.json()),
    fetch('/api/logs').then(r => r.json()),
    fetch('/api/calls').then(r => r.json()),
    fetch('/api/transcripts').then(r => r.json()),
  ]).then(([status, logs, calls, txData]) => {
    updateBadge('sara',  status.sara.status,  status.sara.uptime);
    updateBadge('saima', status.saima.status, status.saima.uptime);
    document.getElementById('uptime-sara').textContent  = status.sara.uptime  || '—';
    document.getElementById('uptime-saima').textContent = status.saima.uptime || '—';
    document.getElementById('calls-sara').textContent   = calls.sara;
    document.getElementById('calls-saima').textContent  = calls.saima;
    document.getElementById('last-updated').textContent = 'Updated ' + status.time;
    renderLog(logs.entries);
    renderTranscripts(txData.calls);
  }).catch(() => {
    document.getElementById('last-updated').textContent = 'Error — retrying…';
  });
}

function control(service, action, btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = action === 'restart' ? 'Restarting…' :
                    action === 'stop'    ? 'Stopping…'   : 'Starting…';
  fetch('/api/control', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({service, action})
  }).then(r => r.json()).then(d => {
    if (!d.ok) alert('Error: ' + (d.error || 'unknown'));
    setTimeout(refresh, 2000);
  }).catch(() => {
    alert('Request failed');
  }).finally(() => {
    btn.disabled = false;
    btn.textContent = orig;
  });
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
