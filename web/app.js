/* Robin Voice Ops — browser voice client for the AssemblyAI Voice Agent API.
   Docs-grounded protocol: temp-token auth (?token=), session.update, input.audio
   (base64 PCM16 24kHz mono), tool.call accumulation with tool.result drained on
   reply.done, barge-in flush, session.resume reconnection, session.end teardown. */
"use strict";

const $ = (id) => document.getElementById(id);
const WS_URL = "wss://agents.assemblyai.com/v1/ws";

const state = {
  ws: null, audioCtx: null, micStream: null, micNode: null,
  sessionId: null, phase: 0, callActive: false,
  pendingTools: [],          // {call_id, name, result} drained on reply.done
  heldCalls: new Map(),      // call_id -> {approval_id, name} awaiting human
  approvalTimer: null,
  playQueue: [], playing: false, sources: [],
  speechStoppedAt: 0, firstAudioAt: 0, latencies: [],
  caller: { name: "", phone: "" },
  demoMode: false,
};

/* ---------- UI helpers ---------- */
function log(kind, text) {
  const feed = $("eventFeed");
  const div = document.createElement("div");
  div.className = "ev-" + kind;
  const t = new Date().toLocaleTimeString();
  div.innerHTML = `<span class="ev-t">${t}</span> ${text}`;
  feed.prepend(div);
  while (feed.children.length > 60) feed.lastChild.remove();
}
function addMsg(who, text) {
  const box = $("transcript");
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.innerHTML = `<span class="who">${who === "user" ? "Caller" : "Robin"}</span><p></p>`;
  div.querySelector("p").textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}
function setStatus(s) { $("callStatus").textContent = s; }
function setLatency(ms) {
  if (!ms) return;
  $("latencyNow").textContent = Math.round(ms) + " ms";
  const arr = state.latencies;
  const p = (q) => arr.length ? Math.round(arr.slice().sort((a, b) => a - b)[Math.min(arr.length - 1, Math.floor(q * arr.length))]) : 0;
  $("latP50").textContent = p(0.5) + " ms";
  $("latP95").textContent = p(0.95) + " ms";
}

/* ---------- audio ---------- */
function b64ToPcm16(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}
function b64Encode(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}
function playPcm16(pcm) {
  const ctx = state.audioCtx;
  const ab = ctx.createBuffer(1, pcm.length, 24000);
  const ch = ab.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
  const src = ctx.createBufferSource();
  src.buffer = ab;
  src.connect(ctx.destination);
  state.sources.push(src);
  state.playQueue.push(src);
  if (!state.playing) pumpQueue();
}
function pumpQueue() {
  const src = state.playQueue.shift();
  if (!src) { state.playing = false; return; }
  state.playing = true;
  src.onended = () => {
    const i = state.sources.indexOf(src);
    if (i >= 0) state.sources.splice(i, 1);
    pumpQueue();
  };
  src.start();
}
function stopPlayback() {  // barge-in: flush scheduled audio
  for (const s of state.sources) { try { s.stop(); } catch (e) {} }
  state.sources = []; state.playQueue = []; state.playing = false;
}

/* ---------- call lifecycle ---------- */
async function startCall() {
  if (state.callActive) return;
  state.caller.name = $("callerName").value.trim();
  state.caller.phone = $("callerPhone").value.trim();
  setStatus("Getting secure token…");
  let tok;
  try {
    const r = await fetch("/api/token");
    tok = await r.json();
    if (!r.ok) throw new Error(tok.error || "token failed");
  } catch (e) {
    setStatus("Voice backend not configured — try Demo mode.");
    log("err", "Token mint failed: " + e.message + ". Use Demo mode or set ASSEMBLYAI_API_KEY.");
    return;
  }
  // Identify caller -> cross-session memory
  let callerCtx = "";
  if (state.caller.phone) {
    try {
      const r = await fetch("/api/memory", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: state.caller.phone, name: state.caller.name }) });
      const d = await r.json();
      callerCtx = d.context || "";
      if (d.profile && d.profile.visit_count > 1)
        log("mem", `Returning caller recognized: ${d.profile.name || d.profile.phone} (${d.profile.visit_count} prior contacts)`);
    } catch (e) { /* memory is best-effort */ }
  }
  const cfg = await (await fetch(`/api/session?phase=0&phone=${encodeURIComponent(state.caller.phone)}`)).json();
  connect(tok.token, cfg, null);
}

function connect(token, sessionCfg, prevSession) {
  const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);
  state.ws = ws;
  ws.onopen = () => {
    if (prevSession) {
      const resumeEv = { type: "session.resume", session_id: prevSession.id };
      if (prevSession.resumeToken) resumeEv.resume_token = prevSession.resumeToken;
      ws.send(JSON.stringify(resumeEv));
      log("sys", "Reconnecting — session.resume sent");
    } else {
      ws.send(JSON.stringify({ type: "session.update", session: sessionCfg.session }));
      log("sys", "session.update sent (phase 0 tools, keyterms, voice_focus)");
    }
  };
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data), sessionCfg);
  ws.onclose = (ev) => {
    if (state.callActive && !state.cleanEnd && state.sessionId) {
      log("sys", "Connection dropped — will attempt session.resume…");
      setTimeout(() => startCallResume(), 1000);
    } else if (state.callActive) {
      endCallUI("Call ended");
    }
  };
  ws.onerror = () => log("err", "WebSocket error");
}

async function startCallResume() {
  // Re-mint a token and resume the previous session within the 30s grace window.
  try {
    const tok = await (await fetch("/api/token")).json();
    const cfg = await (await fetch("/api/session?phase=" + state.phase)).json();
    connect(tok.token, cfg, state.sessionId ? { id: state.sessionId, resumeToken: state.resumeToken } : null);
  } catch (e) { endCallUI("Reconnect failed"); }
}

function handleEvent(ev, sessionCfg) {
  const t = ev.type;
  switch (t) {
    case "session.ready":
      state.sessionId = ev.session_id;
      state.resumeToken = ev.resume_token || null;
      state.callActive = true;
      state.cleanEnd = false;
      setStatus("Live — speak now");
      $("btnCall").disabled = true; $("btnHangup").disabled = false;
      log("sys", "session.ready — mic streaming started");
      startMic();
      break;
    case "session.updated":
      log("sys", "session updated (progressive tool reveal)");
      break;
    case "input.speech.stopped":
      state.speechStoppedAt = performance.now();
      state.firstAudioAt = 0;
      break;
    case "transcript.user.delta":
      $("partialUser").textContent = ev.text || "";
      break;
    case "transcript.user":
      $("partialUser").textContent = "";
      addMsg("user", ev.text || "");
      break;
    case "reply.audio": {
      if (!state.firstAudioAt && state.speechStoppedAt) {
        state.firstAudioAt = performance.now();
        const ms = state.firstAudioAt - state.speechStoppedAt;
        state.latencies.push(ms); setLatency(ms);
      }
      playPcm16(b64ToPcm16(ev.data));
      break;
    }
    case "transcript.agent.delta":
      $("partialAgent").textContent = ( $("partialAgent").textContent + " " + (ev.delta || "")).trim();
      break;
    case "transcript.agent":
      $("partialAgent").textContent = "";
      addMsg("agent", ev.text || "");
      break;
    case "reply.done":
      if (ev.status === "interrupted") {
        stopPlayback();                       // barge-in: flush audio
        state.pendingTools = [];              // discard stale tool results
        log("sys", "Barge-in — reply interrupted, audio flushed");
      } else {
        drainToolResults();                   // accumulate-then-drain pattern
      }
      break;
    case "tool.call":
      onToolCall(ev);
      break;
    case "session.error":
      log("err", "session.error: " + (ev.message || ev.code));
      break;
    case "session.ended":
      state.cleanEnd = true;
      endCallUI("Call ended cleanly");
      break;
  }
}

function drainToolResults() {
  for (const p of state.pendingTools) {
    state.ws.send(JSON.stringify({ type: "tool.result", call_id: p.call_id, result: JSON.stringify(p.result) }));
    log("tool", `tool.result → ${p.name}`);
  }
  state.pendingTools = [];
}

/* tool.call arguments arrive as a parsed dict in current API versions, but be
   liberal: accept a JSON string or the legacy `args` field too. */
function parseToolArgs(ev) {
  const a = ev.arguments ?? ev.args ?? {};
  if (typeof a === "string") {
    try { return JSON.parse(a || "{}"); } catch { return {}; }
  }
  return a && typeof a === "object" ? a : {};
}

async function onToolCall(ev) {
  const { call_id, name } = ev;
  const args = parseToolArgs(ev);
  log("tool", `tool.call ← <b>${name}</b> <code>${JSON.stringify(args).slice(0, 120)}</code>`);
  let res;
  try {
    const r = await fetch("/api/tool", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, arguments: args || {} }) });
    res = await r.json();
  } catch (e) {
    res = { result: { status: "error", message: "Tool backend unreachable." }, held: false };
  }
  const result = res.result || {};
  // Progressive reveal: once real slots exist, expose the booking tool.
  if (name === "check_availability" && state.phase === 0 &&
      result.open_slots && result.open_slots.length) {
    state.phase = 1;
    const cfg = await (await fetch("/api/session?phase=1")).json();
    state.ws.send(JSON.stringify({ type: "session.update", session: { tools: cfg.session.tools } }));
    log("sys", "book_appointment revealed (phase 1)");
  }
  if (res.held) {
    // Gated action: hold the tool call — do NOT send tool.result yet.
    state.heldCalls.set(call_id, { approval_id: res.approval_id, name });
    log("gate", `<b>${name}</b> held — awaiting human approval (approval ${res.approval_id})`);
    refreshApprovals();
    startApprovalPoll();
  } else {
    state.pendingTools.push({ call_id, name, result });
  }
  refreshDashboard();
}

/* Approval polling: when the dispatcher decides, deliver the final tool.result
   so the (silent, hold-mode) agent announces the outcome. */
function startApprovalPoll() {
  if (state.approvalTimer) return;
  state.approvalTimer = setInterval(async () => {
    if (!state.heldCalls.size) { clearInterval(state.approvalTimer); state.approvalTimer = null; return; }
    try {
      const pend = new Set((await (await fetch("/api/approvals")).json()).pending.map((a) => a.id));
      for (const [call_id, h] of [...state.heldCalls]) {
        if (!pend.has(h.approval_id)) {
          // Decided — fetch the final tool.result payload and deliver it.
          const dec = await (await fetch(`/api/approvals?id=${encodeURIComponent(h.approval_id)}`)).json();
          state.heldCalls.delete(call_id);
          if (state.ws && state.ws.readyState === 1 && dec.tool_result) {
            state.ws.send(JSON.stringify({ type: "tool.result", call_id, result: JSON.stringify(dec.tool_result) }));
            log("gate", `Approval ${dec.approval.status} → tool.result sent for ${h.name}`);
          }
          refreshApprovals(); refreshAudit(); refreshDashboard();
        }
      }
    } catch (e) { /* keep polling */ }
  }, 2000);
}
async function startMic() {
  try {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    await state.audioCtx.audioWorklet.addModule("/worklets/pcm-capture.js");
    state.micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const src = state.audioCtx.createMediaStreamSource(state.micStream);
    state.micNode = new AudioWorkletNode(state.audioCtx, "pcm-capture");
    state.micNode.port.onmessage = (e) => {
      if (state.ws && state.ws.readyState === 1 && state.callActive)
        state.ws.send(JSON.stringify({ type: "input.audio", audio: b64Encode(e.data) }));
    };
    src.connect(state.micNode);
  } catch (e) {
    log("err", "Mic unavailable: " + e.message + ". Try Demo mode.");
  }
}

function hangup() {
  if (state.ws && state.ws.readyState === 1) {
    try { state.ws.send(JSON.stringify({ type: "session.end" })); } catch (e) {}
    state.cleanEnd = true;
    setTimeout(() => { try { state.ws.close(); } catch (e) {} }, 400);
  }
  endCallUI("Call ended");
}
function endCallUI(msg) {
  state.callActive = false; state.cleanEnd = false;
  stopPlayback();
  if (state.micStream) state.micStream.getTracks().forEach((t) => t.stop());
  if (state.audioCtx) state.audioCtx.close().catch(() => {});
  $("btnCall").disabled = false; $("btnHangup").disabled = true;
  setStatus(msg);
  log("sys", msg);
}

/* ---------- dashboard ---------- */
async function refreshApprovals() {
  try {
    const d = await (await fetch("/api/approvals")).json();
    const box = $("approvals");
    box.innerHTML = "";
    if (!d.pending.length) { box.innerHTML = `<div class="empty">No pending approvals. Gated actions (bookings, escalations) will appear here.</div>`; return; }
    for (const ap of d.pending) {
      const p = ap.payload || {};
      const div = document.createElement("div");
      div.className = "appr";
      div.innerHTML = `<div><b>${ap.action}</b> <span class="tag">${ap.kind}</span><br>
        <small>${(p.customer || p.name || "")} ${p.phone || p.callback_number || ""} ${p.slot_id || ""}</small><br>
        <small class="muted">${new Date(ap.created_at * 1000).toLocaleTimeString()} · ${ap.id}</small></div>
        <div class="appr-btns"><button data-a="${ap.id}" data-ok="1" class="ok">Approve</button>
        <button data-a="${ap.id}" data-ok="0" class="no">Reject</button></div>`;
      box.appendChild(div);
    }
    box.querySelectorAll("button").forEach((b) => b.onclick = () => decideApproval(b.dataset.a, b.dataset.ok === "1"));
  } catch (e) {}
}
async function decideApproval(id, ok) {
  await fetch("/api/approvals", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_id: id, approved: ok, decided_by: "dispatcher" }) });
  log("gate", `Approval ${id} ${ok ? "APPROVED" : "REJECTED"} by dispatcher`);
  refreshApprovals(); refreshDashboard(); refreshAudit();
}
async function refreshAudit() {
  try {
    const d = await (await fetch("/api/audit?limit=30")).json();
    $("auditOk").textContent = d.verification.ok ? `chain intact · ${d.verification.entries} entries` : "CHAIN BROKEN";
    $("auditOk").className = d.verification.ok ? "ok-pill" : "bad-pill";
    const box = $("auditLog"); box.innerHTML = "";
    for (const e of d.entries) {
      const div = document.createElement("div");
      div.className = "audit-row";
      div.innerHTML = `<span class="ev-t">${new Date(e.ts * 1000).toLocaleTimeString()}</span>
        <b>${e.actor}</b> ${e.action} <code>${e.hash.slice(0, 10)}…</code>`;
      box.appendChild(div);
    }
  } catch (e) {}
}
async function refreshDashboard() {
  try {
    const d = await (await fetch("/api/dashboard")).json();
    const fmt = (rows, cols) => rows.map((r) =>
      `<tr>${cols.map((c) => `<td>${r[c] ?? ""}</td>`).join("")}</tr>`).join("");
    $("tblAppt").innerHTML = fmt(d.appointments, ["id", "customer_name", "service", "slot_start", "status"]);
    $("tblReq").innerHTML = fmt(d.service_requests, ["id", "category", "priority", "status"]);
    $("tblJobs").innerHTML = fmt(d.jobs, ["id", "customer_name", "service", "status", "technician"]);
  } catch (e) {}
}

/* ---------- demo mode (scripted, no mic/key) ---------- */
const DEMO_SCRIPT = [
  ["user", "Hi, my water heater is leaking. Can someone come tomorrow morning?"],
  ["tool", "check_availability", '{"service":"Plumbing","time_window":"morning"}'],
  ["agent", "I found a plumbing slot tomorrow morning, 8 to 12. Want me to request it? It'll need our dispatcher's approval to confirm."],
  ["user", "Yes please. I'm Maria Lopez, 415-555-0101."],
  ["tool", "book_appointment", '{"service":"Plumbing","slot_id":"slot_16_morning","name":"Maria Lopez"}'],
  ["gate", "Booking held — awaiting dispatcher approval…"],
  ["agent", "Request sent — it's pending dispatcher approval. You'll get a confirmation once they approve it."],
];
async function runDemo() {
  if (state.demoMode) return;
  state.demoMode = true;
  $("btnDemo").disabled = true;
  setStatus("Demo mode — scripted walkthrough");
  for (const [kind, a, b] of DEMO_SCRIPT) {
    await new Promise((r) => setTimeout(r, 1400));
    if (kind === "user") addMsg("user", a);
    else if (kind === "agent") addMsg("agent", a);
    else if (kind === "tool") log("tool", `tool.call ← <b>${a}</b> <code>${b}</code>`);
    else log("gate", a);
  }
  refreshApprovals(); refreshAudit(); refreshDashboard();
  setStatus("Demo complete — approve the pending booking above");
  state.demoMode = false;
  $("btnDemo").disabled = false;
}

/* ---------- personas ---------- */
function fillPersona(which) {
  if (which === "maria") { $("callerName").value = "Maria Lopez"; $("callerPhone").value = "+14155550101"; }
  if (which === "james") { $("callerName").value = "James Chen"; $("callerPhone").value = "+14155550102"; }
  if (which === "new") { $("callerName").value = ""; $("callerPhone").value = ""; }
}

/* ---------- wire up ---------- */
$("btnCall").onclick = startCall;
$("btnHangup").onclick = hangup;
$("btnDemo").onclick = runDemo;
$("tabCall").onclick = () => showTab("call");
$("tabOps").onclick = () => showTab("ops");
function showTab(which) {
  $("tabCall").classList.toggle("active", which === "call");
  $("tabOps").classList.toggle("active", which === "ops");
  $("paneCall").style.display = which === "call" ? "" : "none";
  $("paneOps").style.display = which === "ops" ? "" : "none";
  if (which === "ops") { refreshApprovals(); refreshAudit(); refreshDashboard(); }
}
setInterval(() => { if ($("paneOps").style.display !== "none") refreshApprovals(); }, 5000);
refreshApprovals(); refreshAudit(); refreshDashboard();
