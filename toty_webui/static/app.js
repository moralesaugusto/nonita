"use strict";

const SETTINGS_KEY = "toty-webui.settings.v1";
const $ = (id) => document.getElementById(id);

const state = {
  history: [],          // [{role, content, thinking?, thinkingSecs?, footer?, error?}]
  convId: null,
  usage: { prompt: 0, completion: 0, turns: 0 },
  uploads: [],          // [{id, name, size}]
  sessions: [],
  selectedSession: null,
  controller: null,
  cfg: null,
  metricsEmpty: "",
};

// ── helpers ────────────────────────────────────────────────────────────
function md(text) {
  return DOMPurify.sanitize(marked.parse(text || "", { breaks: true }), { ADD_ATTR: ["target"] });
}
function setMd(el, text) { el.innerHTML = md(text); }
function conn() { return { host: $("host-in").value, port: $("port-in").value === "" ? null : Number($("port-in").value) }; }
async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) {}
    throw new Error(detail);
  }
  return res;
}
async function apiJson(path, body, method) { return (await api(path, body, method)).json(); }
function toggleInfo(el, text) { el.hidden = false; setMd(el, text); }
async function download(res, name) {
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── chat rendering ─────────────────────────────────────────────────────
function renderMessage(m, live) {
  const div = document.createElement("div");
  div.className = "msg " + (m.role === "user" ? "user" : "assistant") + (m.error ? " error" : "");
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = m.role === "user" ? "you" : "assistant";
  div.appendChild(who);
  if (m.thinking) {
    const d = document.createElement("details");
    d.className = "thought";
    if (live && !m.thinkingSecs) d.open = true;
    const s = document.createElement("summary");
    s.textContent = m.thinkingSecs ? `🤔 Thought for ${m.thinkingSecs.toFixed(1)}s` : "🤔 Thinking…";
    const body = document.createElement("div");
    body.className = "md";
    setMd(body, m.thinking);
    d.append(s, body);
    div.appendChild(d);
  }
  const body = document.createElement("div");
  body.className = "md body";
  setMd(body, m.content);
  div.appendChild(body);
  if (m.footer) {
    const f = document.createElement("div");
    f.className = "footer";
    f.textContent = m.footer;
    div.appendChild(f);
  }
  return div;
}
function renderChat() {
  const chat = $("chat");
  chat.replaceChildren(...state.history.map((m) => renderMessage(m, false)));
  chat.scrollTop = chat.scrollHeight;
}
function updateLast() {
  const chat = $("chat");
  const last = chat.lastElementChild;
  const fresh = renderMessage(state.history[state.history.length - 1], true);
  if (last) last.replaceWith(fresh); else chat.appendChild(fresh);
  chat.scrollTop = chat.scrollHeight;
}
function convBar() {
  const u = state.usage;
  if (!u.turns) { $("conv-bar").textContent = ""; return; }
  const total = u.prompt + u.completion;
  let t = `Session: **${u.turns}** ${u.turns === 1 ? "turn" : "turns"}`;
  if (total) t += ` · ↑${u.prompt.toLocaleString()} ↓${u.completion.toLocaleString()} tok · **${total.toLocaleString()} total**`;
  setMd($("conv-bar"), t);
}
function resetMetrics() { setMd($("metrics-bar"), state.metricsEmpty); $("debug-panel").textContent = ""; }

// ── connection / models ────────────────────────────────────────────────
async function refreshModels() {
  try {
    const r = await apiJson("/api/models", { ...conn(), current_model: $("model-input").value || null });
    const dl = $("model-list");
    dl.replaceChildren(...r.choices.map((c) => { const o = document.createElement("option"); o.value = c; return o; }));
    if (r.selected) $("model-input").value = r.selected;
    setMd($("status-note"), r.note);
    setMd($("connection-badge"), r.badge);
  } catch (e) {
    setMd($("status-note"), "Could not refresh: `" + e.message + "`");
  }
}
async function simple(path, extra) {
  try { return await apiJson(path, { ...conn(), ...(extra || {}) }); }
  catch (e) { return { markdown: "Request failed: `" + e.message + "`", note: e.message, badge: "" }; }
}

// ── browser-local settings (host/port only; never secrets) ─────────────
function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (s && typeof s.host === "string" && Number.isInteger(s.port)) {
      $("host-in").value = s.host; $("port-in").value = s.port;
    }
  } catch (e) {}
}
function forgetSettings() { try { localStorage.removeItem(SETTINGS_KEY); } catch (e) {} }
async function saveSettings() {
  const v = await apiJson("/api/validate-connection", conn());
  if (!v.ok) { $("settings-status").textContent = "Not saved: " + v.error; return; }
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ v: 1, host: $("host-in").value.trim(), port: Number($("port-in").value) }));
    $("settings-status").textContent = "Saved locally in this browser.";
  } catch (e) { $("settings-status").textContent = "Not saved: browser storage is unavailable."; }
}
function resetSettings() {
  forgetSettings();
  $("host-in").value = state.cfg.host; $("port-in").value = state.cfg.port;
  $("settings-status").textContent = "Reset to defaults; saved settings removed.";
}

// ── sessions ───────────────────────────────────────────────────────────
function renderSessions() {
  const box = $("sessions-list");
  box.replaceChildren(...state.sessions.map((s) => {
    const b = document.createElement("button");
    b.className = "session-item" + (s.id === state.selectedSession ? " selected" : "");
    b.setAttribute("role", "option");
    b.textContent = s.label;
    b.onclick = () => openSession(s.id);
    return b;
  }));
}
async function loadSessions() { state.sessions = await apiJson("/api/sessions"); renderSessions(); }
async function openSession(id) {
  state.selectedSession = id; renderSessions();
  try {
    const c = await apiJson("/api/sessions/" + id);
    state.history = c.messages.map((m) => ({ ...m, content: typeof m.content === "string" ? m.content : String(m.content ?? "") }));
    state.convId = id; state.usage = { prompt: 0, completion: 0, turns: 0 };
    if (c.model) $("model-input").value = c.model;
    renderChat(); convBar(); $("history-status").textContent = `Opened conversation #${id}.`;
  } catch (e) { $("history-status").textContent = e.message; }
}
function newConversation() {
  stopStream();
  state.history = []; state.convId = null; state.usage = { prompt: 0, completion: 0, turns: 0 };
  $("msg").value = ""; renderChat(); convBar(); resetMetrics();
}

// ── attachments ────────────────────────────────────────────────────────
function renderAttachments() {
  const el = $("attach-summary");
  if (!state.uploads.length) { setMd(el, "_No files attached — context applies to the next message only._"); return; }
  const names = state.uploads.slice(0, 5).map((u) => `**${u.name}**`).join(", ");
  const more = state.uploads.length > 5 ? `, +${state.uploads.length - 5} more` : "";
  setMd(el, `**${state.uploads.length}** attached: ${names}${more}`);
}
async function uploadFiles(fileList) {
  if (!fileList.length) return;
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error(res.statusText);
    const r = await res.json();
    state.uploads.push(...r.files);
    renderAttachments();
    if (r.rejected.length) {
      const el = $("attach-summary");
      el.innerHTML += md(r.rejected.map((x) => `Skipped **${x.name}**: ${x.reason}`).join("\n\n"));
    }
  } catch (e) { setMd($("attach-summary"), "Upload failed: `" + e.message + "`"); }
  $("file-input").value = "";
}

// ── chat ───────────────────────────────────────────────────────────────
function setBusy(busy) {
  $("send-btn").disabled = busy; $("stop-btn").disabled = !busy;
}
function stopStream() { if (state.controller) state.controller.abort(); }

async function send() {
  const text = $("msg").value.trim();
  if (!text || state.controller) return;
  const prior = state.history.filter((m) => !m.error).map(({ role, content }) => ({ role, content }));
  state.history.push({ role: "user", content: text });
  const reply = { role: "assistant", content: "" };
  state.history.push(reply);
  $("msg").value = ""; autosize();
  renderChat();
  const think = document.querySelector("input[name=think]:checked").value;
  const body = {
    ...conn(), message: text, history: prior, model: $("model-input").value.trim() || null,
    system_prompt: $("system-prompt").value, temperature: Number($("temperature").value),
    num_ctx: Number($("num-ctx").value) || 0, stream_delay_ms: Number($("stream-delay").value),
    upload_ids: state.uploads.map((u) => u.id), debug_mode: $("debug-mode").checked,
    search_enabled: $("search-enabled").checked, search_max_results: Number($("search-max").value),
    think_mode: think,
  };
  state.controller = new AbortController();
  setBusy(true);
  const started = performance.now();
  let thinkStart = null;
  let aborted = false;
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: state.controller.signal,
    });
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const handle = (ev) => {
      if (ev.type === "status") { setMd($("metrics-bar"), ev.text); return; }
      if (ev.type === "thinking") {
        if (thinkStart === null) thinkStart = performance.now();
        reply.thinking = (reply.thinking || "") + ev.text;
      } else if (ev.type === "text") {
        if (thinkStart !== null && !reply.thinkingSecs) reply.thinkingSecs = (performance.now() - thinkStart) / 1000;
        reply.content += ev.text;
      } else if (ev.type === "done") {
        if (thinkStart !== null && !reply.thinkingSecs) reply.thinkingSecs = (performance.now() - thinkStart) / 1000;
        reply.footer = ev.footer || "";
        state.usage.prompt += ev.usage.prompt; state.usage.completion += ev.usage.completion; state.usage.turns += 1;
        setMd($("metrics-bar"), ev.metrics); $("debug-panel").textContent = ""; if (ev.debug) setMd($("debug-panel"), ev.debug);
        convBar();
      } else if (ev.type === "error") {
        reply.error = true;
        reply.content = ev.message;
        if (ev.metrics) setMd($("metrics-bar"), ev.metrics);
        if (ev.debug) setMd($("debug-panel"), ev.debug);
      }
      updateLast();
      if (ev.type === "text" || ev.type === "thinking") {
        setMd($("metrics-bar"), `⏱ **${((performance.now() - started) / 1000).toFixed(1)}s** · streaming…`);
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (line) handle(JSON.parse(line));
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      aborted = true;
      reply.content = (reply.content ? reply.content.trimEnd() + "\n\n" : "") + "*[generation stopped]*";
    } else {
      reply.error = true; reply.content = `**Request failed:** \`${e.message}\``;
    }
    updateLast();
  } finally {
    state.controller = null;
    setBusy(false);
    if (aborted) setMd($("metrics-bar"), `⏱ **${((performance.now() - started) / 1000).toFixed(1)}s** · *stopped early*`);
  }
}

// ── misc UI ────────────────────────────────────────────────────────────
function autosize() {
  const t = $("msg"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 144) + "px";
}
function bindRange(id, outId, fmt) {
  const el = $(id), out = $(outId);
  const upd = () => { out.textContent = fmt ? fmt(el.value) : el.value; };
  el.addEventListener("input", upd); upd();
}

async function init() {
  bindRange("temperature", "temperature-val", (v) => Number(v).toFixed(2));
  bindRange("stream-delay", "stream-delay-val");
  bindRange("search-max", "search-max-val");

  const [meta, cfg] = await Promise.all([apiJson("/api/meta"), apiJson("/api/config")]);
  state.cfg = cfg; state.metricsEmpty = cfg.metrics_empty;
  document.title = meta.title;
  $("app-version").textContent = `${meta.title} v${meta.version}`;
  const tg = $("app-telegram");
  tg.textContent = `Telegram ${meta.telegram}`; tg.href = meta.telegram_url;
  $("host-in").value = cfg.host; $("port-in").value = cfg.port; $("host-in").placeholder = cfg.default_host;
  $("debug-mode").checked = cfg.debug; $("debug-panel").hidden = !cfg.debug;
  loadSettings();                       // restore saved host/port BEFORE the first refresh
  resetMetrics(); renderAttachments(); renderChat();

  $("btn-refresh").onclick = refreshModels;
  $("btn-ping").onclick = async () => { const r = await simple("/api/ping"); setMd($("status-note"), r.note); setMd($("connection-badge"), r.badge); };
  $("btn-loaded").onclick = async () => setMd($("status-note"), (await simple("/api/loaded")).markdown);
  $("btn-unload").onclick = async () => setMd($("status-note"), (await simple("/api/unload", { model: $("model-input").value || null })).markdown);
  $("btn-unload-all").onclick = async () => setMd($("status-note"), (await simple("/api/unload-all")).markdown);
  $("btn-ollama-info").onclick = async () => toggleInfo($("ollama-info-panel"), (await simple("/api/server-info")).markdown);
  $("btn-model-info").onclick = async () => toggleInfo($("model-info-panel"), (await simple("/api/model-info", { model: $("model-input").value || null })).markdown);
  $("btn-save-settings").onclick = saveSettings;
  $("btn-reset-settings").onclick = resetSettings;
  $("btn-forget-settings").onclick = () => { forgetSettings(); $("settings-status").textContent = "Saved settings removed; current values kept until reload."; };

  $("send-btn").onclick = send;
  $("stop-btn").onclick = stopStream;
  $("msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $("msg").addEventListener("input", autosize);
  $("btn-clear").onclick = newConversation;
  $("btn-new").onclick = newConversation;
  $("btn-clear-files").onclick = () => { state.uploads = []; $("file-input").value = ""; renderAttachments(); };
  $("btn-example").onclick = () => { $("msg").value = "Summarize the attached files in three bullet points, then suggest logical next steps."; autosize(); $("msg").focus(); };
  $("file-input").onchange = (e) => uploadFiles([...e.target.files]);

  $("btn-save").onclick = async () => {
    if (!state.history.length) { $("history-status").textContent = "Nothing to save — chat is empty."; return; }
    try {
      const messages = state.history.filter((m) => !m.error);
      const r = await apiJson("/api/sessions", { conv_id: state.convId, model: $("model-input").value || null, messages });
      state.convId = r.id; await loadSessions(); $("history-status").textContent = `Saved conversation #${r.id}.`;
    } catch (e) { $("history-status").textContent = e.message; }
  };
  $("btn-refresh-sessions").onclick = loadSessions;
  $("btn-delete-session").onclick = async () => {
    if (state.selectedSession == null) { $("history-status").textContent = "Pick a conversation first."; return; }
    const id = state.selectedSession;
    await apiJson("/api/sessions/" + id, undefined, "DELETE");
    if (state.convId === id) state.convId = null;
    state.selectedSession = null; await loadSessions(); $("history-status").textContent = `Deleted conversation #${id}.`;
  };
  $("btn-export-session").onclick = async () => {
    if (state.selectedSession == null) { $("history-status").textContent = "Pick a conversation first."; return; }
    try { await download(await api(`/api/sessions/${state.selectedSession}/export`), `toty_conversation_${state.selectedSession}.txt`); }
    catch (e) { $("history-status").textContent = e.message; }
  };
  $("btn-export-txt").onclick = async () => {
    if (!state.history.length) { $("history-status").textContent = "Nothing to export — chat is empty."; return; }
    try {
      const messages = state.history.filter((m) => !m.error);
      await download(await api("/api/export", { model: $("model-input").value || null, messages }), "toty_conversation.txt");
      $("history-status").textContent = "Exported current chat.";
    } catch (e) { $("history-status").textContent = e.message; }
  };
  $("btn-wipe").onclick = async () => {
    if (!$("wipe-confirm").checked) { $("history-status").textContent = "Check the confirm box first — nothing was deleted."; return; }
    const r = await apiJson("/api/sessions?confirm=true", undefined, "DELETE");
    $("wipe-confirm").checked = false; state.convId = null; state.selectedSession = null;
    await loadSessions(); $("history-status").textContent = r.deleted ? `Deleted ${r.deleted} saved conversation(s).` : "Database was already empty.";
  };
  $("debug-mode").onchange = (e) => { $("debug-panel").hidden = !e.target.checked; };

  await Promise.all([refreshModels(), loadSessions()]);
}
init();
