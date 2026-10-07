"use strict";

const SETTINGS_KEY = "nonita.settings.v1";
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
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      if (j.code === "auth_required") showLogin();
      if (j.code === "password_change_required") showPasswordForm(true);
    } catch (e) {}
    throw new Error(detail);
  }
  return res;
}

// ── sign-in / password change ──────────────────────────────────────────
let appStarted = false;
let currentUser = "";
function showOverlay(which) {
  $("auth-overlay").hidden = false;
  $("login-form").hidden = which !== "login";
  $("pw-form").hidden = which !== "pw";
  $(which === "login" ? "login-user" : "pw-current").focus();
}
function showLogin() {
  $("login-pass").value = ""; $("login-error").textContent = "";
  $("btn-account").hidden = $("btn-logout").hidden = true;
  showOverlay("login");
}
function showPasswordForm(forced) {
  for (const id of ["pw-current", "pw-new", "pw-confirm"]) $(id).value = "";
  $("pw-error").textContent = ""; $("pw-user").value = currentUser;
  $("pw-forced").hidden = !forced; $("pw-cancel").hidden = forced;
  $("pw-title").textContent = forced ? "Set a new password" : "Change password";
  showOverlay("pw");
}
async function afterAuth(user) {
  currentUser = user.username;
  $("btn-account").hidden = $("btn-logout").hidden = false;
  if (user.must_change) { showPasswordForm(true); return; }
  $("auth-overlay").hidden = true;
  if (!appStarted) { appStarted = true; await startApp(); }
}
async function postAuth(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let j = {};
  try { j = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : res.statusText);
  return j;
}
function bindAuth() {
  $("login-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await postAuth("/api/auth/login", { username: $("login-user").value.trim(), password: $("login-pass").value });
      $("login-pass").value = "";
      await afterAuth(r);
    } catch (err) { $("login-error").textContent = err.message; }
  };
  $("pw-form").onsubmit = async (e) => {
    e.preventDefault();
    if ($("pw-new").value !== $("pw-confirm").value) { $("pw-error").textContent = "The new passwords do not match."; return; }
    try {
      await postAuth("/api/auth/password", { current_password: $("pw-current").value, new_password: $("pw-new").value });
      await afterAuth({ username: currentUser, must_change: false });
    } catch (err) { $("pw-error").textContent = err.message; }
  };
  $("pw-cancel").onclick = () => { $("auth-overlay").hidden = true; };
  $("btn-account").onclick = () => showPasswordForm(false);
  $("btn-logout").onclick = async () => {
    try { await postAuth("/api/auth/logout", {}); } catch (e) {}
    location.reload();
  };
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
  who.textContent = m.role === "user" ? "You" : "Assistant";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (m.thinking) {
    const d = document.createElement("details");
    d.className = "thought";
    if (live && !m.thinkingSecs) d.open = true;
    const sm = document.createElement("summary");
    sm.textContent = m.thinkingSecs ? `Thought for ${m.thinkingSecs.toFixed(1)}s` : "Thinking…";
    const tb = document.createElement("div");
    tb.className = "md";
    setMd(tb, m.thinking);
    d.append(sm, tb);
    bubble.appendChild(d);
  }
  const body = document.createElement("div");
  body.className = "md body";
  if (live && !m.content && !m.thinking) body.textContent = "…";
  else setMd(body, m.content);
  bubble.appendChild(body);
  div.append(who, bubble);
  if (m.searchNote) {
    const n = document.createElement("div");
    n.className = "search-note" + (m.searchEmpty ? " warn" : "");
    n.textContent = m.searchNote;
    div.appendChild(n);
  }
  if (m.footer) {
    const f = document.createElement("div");
    f.className = "footer";
    f.textContent = m.footer;
    div.appendChild(f);
  }
  return div;
}
function emptyState() {
  const d = document.createElement("div");
  d.className = "empty";
  d.innerHTML = '<svg class="i"><use href="#i-chat"/></svg><h2>Start a conversation</h2>' +
    "<p>Pick a model, then type a message below. Attach files or enable web search from the settings panel.</p>";
  return d;
}
function renderChat() {
  const chat = $("chat");
  chat.replaceChildren(...(state.history.length ? state.history.map((m) => renderMessage(m, false)) : [emptyState()]));
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

function setBadge(text) {
  const el = $("connection-badge");
  const t = text || "";
  el.classList.remove("ok", "bad");
  if (t.includes("🟢")) el.classList.add("ok");
  else if (t.includes("🔴")) el.classList.add("bad");
  const clean = t.replace(/[\u{1F300}-\u{1FAFF}\u2600-\u27BF\uFE0F]/gu, "").replace(/\*\*/g, "").replace(/`/g, "").trim();
  el.querySelector(".badge-text").textContent = clean;
  el.title = clean;
}

// ── connection / models ────────────────────────────────────────────────
async function refreshModels() {
  try {
    const r = await apiJson("/api/models", { ...conn(), current_model: $("model-input").value || null });
    const dl = $("model-list");
    dl.replaceChildren(...r.choices.map((c) => { const o = document.createElement("option"); o.value = c; return o; }));
    if (r.selected) $("model-input").value = r.selected;
    setMd($("status-note"), r.note);
    setBadge(r.badge);
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
  if (!state.sessions.length) {
    const p = document.createElement("div"); p.className = "session-empty"; p.textContent = "No saved conversations yet.";
    box.replaceChildren(p); return;
  }
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
// Messages sent to save/export: no errors, no transient search notes (search stays ephemeral).
function persistable() {
  return state.history.filter((m) => !m.error).map(({ searchNote, searchEmpty, ...rest }) => rest);
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
    if (res.status === 401) { showLogin(); throw new Error("Session expired, sign in again."); }
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
  const prior = persistable().map(({ role, content }) => ({ role, content }));
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
  // The server deletes attachments after this message, so they apply to this message only.
  state.uploads = []; $("file-input").value = ""; renderAttachments();
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
    if (res.status === 401) { showLogin(); throw new Error("Session expired, sign in again."); }
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const handle = (ev) => {
      if (ev.type === "status") { setMd($("metrics-bar"), ev.text); return; }
      if (ev.type === "search") {
        reply.searchNote = ev.count > 0
          ? `Web search: ${ev.count} result${ev.count === 1 ? "" : "s"} used`
          : "Web search returned no results (rate-limited, timed out or offline) — answered without it";
        reply.searchEmpty = ev.count === 0;
        if (ev.count === 0) reply.searchQuery = ev.query;  // kept with the saved chat; erased by Delete / Wipe
      } else if (ev.type === "thinking") {
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
  const t = $("msg"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; t.style.overflowY = t.scrollHeight > 160 ? "auto" : "hidden";
}
function bindRange(id, outId, fmt) {
  const el = $(id), out = $(outId);
  const upd = () => { out.textContent = fmt ? fmt(el.value) : el.value; };
  el.addEventListener("input", upd); upd();
}

// ── theme (light / dark, remembered per browser) ───────────────────────
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("theme-icon").setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon");
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem("nonita.theme"); } catch (e) {}
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(t);
  $("theme-toggle").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("nonita.theme", next); } catch (e) {}
  };
}

async function init() {
  initTheme();
  bindAuth();
  const me = await (await fetch("/api/auth/me")).json();
  if (me.authenticated) await afterAuth(me); else showLogin();
}
async function startApp() {
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
  $("btn-ping").onclick = async () => { const r = await simple("/api/ping"); setMd($("status-note"), r.note); setBadge(r.badge); };
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
  $("btn-clear-files").onclick = () => {
    for (const u of state.uploads) fetch("/api/upload/" + u.id, { method: "DELETE" }).catch(() => {});
    state.uploads = []; $("file-input").value = ""; renderAttachments(); };
  $("btn-example").onclick = () => { $("msg").value = "Summarize the attached files in three bullet points, then suggest logical next steps."; autosize(); $("msg").focus(); };
  $("file-input").onchange = (e) => uploadFiles([...e.target.files]);

  $("btn-save").onclick = async () => {
    if (!state.history.length) { $("history-status").textContent = "Nothing to save — chat is empty."; return; }
    try {
      const messages = persistable();
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
    try { await download(await api(`/api/sessions/${state.selectedSession}/export`), `nonita_conversation_${state.selectedSession}.txt`); }
    catch (e) { $("history-status").textContent = e.message; }
  };
  $("btn-export-txt").onclick = async () => {
    if (!state.history.length) { $("history-status").textContent = "Nothing to export — chat is empty."; return; }
    try {
      const messages = persistable();
      await download(await api("/api/export", { model: $("model-input").value || null, messages }), "nonita_conversation.txt");
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
