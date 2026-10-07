"""Gradio frontend for Ollama (streaming chat, model discovery, file context)."""

from __future__ import annotations

import logging
import os

import gradio as gr

from toty_webui import history as history_store
from toty_webui.connection import parse_connection
from toty_webui.config import DEFAULT_HOST, env_debug_mode, env_host, env_port, gradio_server_port, ssl_launch_kwargs
from toty_webui.metrics import empty_metrics_display
from toty_webui.files import summarize_upload_paths
from toty_webui.handlers import (
    clear_chat,
    fetch_models,
    insert_example_prompt,
    list_loaded_models,
    ollama_server_info,
    pick_model_value,
    ping_host,
    request_stop,
    show_model_info,
    stream_reply,
    unload_all_loaded_models,
    unload_model,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

APP_TITLE = "Toty"

CUSTOM_CSS = """
/* ── Palette ─────────────────────────────────────────── */
:root {
    --hk-bg: #060a06;
    --hk-panel: #0a0f0a;
    --hk-panel-alt: #0d140d;
    --hk-border: #1d5c33;
    --hk-border-bright: #2fdc6c;
    --hk-green: #39ff6a;
    --hk-green-dim: #1f8f45;
    --hk-green-glow: rgba(57,255,106,0.35);
    --hk-cyan: #4dfaff;
    --hk-red: #ff3b57;
    --hk-mono: 'Share Tech Mono', 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* ── Global terminal backdrop ──────────────────────────── */
html, body, .gradio-container, .dark, .dark .gradio-container {
    background: var(--hk-bg) !important;
    color: var(--hk-green) !important;
    font-family: var(--hk-mono) !important;
}
.gradio-container {
    max-width: 1600px !important;
    width: 96vw !important;
    margin: 0 auto !important;
    padding: 0 1rem !important;
    position: relative;
}
* { font-family: var(--hk-mono) !important; }

/* faint CRT scanlines over the whole app */
body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 9999;
    background: repeating-linear-gradient(
        to bottom,
        rgba(57,255,106,0.035) 0px,
        rgba(57,255,106,0.035) 1px,
        transparent 1px,
        transparent 3px
    );
    mix-blend-mode: overlay;
    animation: hk-flicker 7s infinite;
}
@keyframes hk-flicker {
    0%, 96%, 100% { opacity: 1; }
    97% { opacity: 0.85; }
    98% { opacity: 1; }
}
@keyframes hk-blink {
    0%, 49% { opacity: 1; }
    50%, 100% { opacity: 0; }
}

/* ── Panels / blocks / accordions ──────────────────────── */
.block, .form, .panel, .gr-panel, .gr-group,
label, fieldset {
    background: var(--hk-panel) !important;
    border-color: var(--hk-border) !important;
    color: var(--hk-green) !important;
}
label span, .label-wrap span, legend span {
    color: var(--hk-green-dim) !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.9rem !important;
}
.label-wrap > button::before,
.accordion-container > .label-wrap > span::before {
    content: ">_ ";
    color: var(--hk-border-bright);
}

/* ── Inputs ─────────────────────────────────────────────── */
input, textarea, select, .gr-box {
    /* background-color, not the `background` shorthand — the shorthand resets
       background-image to none, which silently erases the checkbox/radio
       checked-state icon that Gradio draws via background-image */
    background-color: #000000 !important;
    color: var(--hk-green) !important;
    border: 1px solid var(--hk-border) !important;
    caret-color: var(--hk-green);
    font-size: 1rem !important;
}

/* ── Checkboxes (checked state must stay visible) ──────── */
input[type="checkbox"] {
    accent-color: var(--hk-green);
    cursor: pointer;
}
input[type="checkbox"]:checked {
    background-color: var(--hk-green) !important;
    border-color: var(--hk-border-bright) !important;
}
input::placeholder, textarea::placeholder {
    color: var(--hk-green-dim) !important;
    opacity: 0.8;
}
input:focus, textarea:focus, select:focus {
    border-color: var(--hk-border-bright) !important;
    box-shadow: 0 0 0 1px var(--hk-border-bright), 0 0 10px var(--hk-green-glow) !important;
}

/* ── Buttons ────────────────────────────────────────────── */
button {
    background: #000000 !important;
    color: var(--hk-green) !important;
    border: 1px solid var(--hk-border) !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600 !important;
    font-size: 1rem !important;
    transition: box-shadow 0.15s ease, background 0.15s ease, color 0.15s ease !important;
}
button:hover {
    background: var(--hk-green) !important;
    color: #000000 !important;
    box-shadow: 0 0 14px var(--hk-green-glow) !important;
}
button.primary {
    border-color: var(--hk-border-bright) !important;
    box-shadow: 0 0 8px var(--hk-green-glow) !important;
}
button.stop, button.stop:hover {
    border-color: var(--hk-red) !important;
}
button.stop { color: var(--hk-red) !important; }
button.stop:hover { background: var(--hk-red) !important; color: #000 !important; box-shadow: 0 0 14px rgba(255,59,87,0.45) !important; }

/* ── Scrollbars ─────────────────────────────────────────── */
* { scrollbar-color: var(--hk-border-bright) #000; scrollbar-width: thin; }
*::-webkit-scrollbar { width: 9px; height: 9px; }
*::-webkit-scrollbar-track { background: #000; }
*::-webkit-scrollbar-thumb { background: var(--hk-border); }
*::-webkit-scrollbar-thumb:hover { background: var(--hk-border-bright); }

/* ── App shell (sessions rail | chat | settings) ───────── */
#app-shell { gap: 1rem !important; }

/* ── Sessions rail ──────────────────────────────────────── */
#sessions-rail {
    background: var(--hk-panel-alt) !important;
    border: 1px solid var(--hk-border) !important;
    border-radius: 4px !important;
    padding: 1rem 0.85rem !important;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
#rail-title { margin-bottom: 0.3rem; }
#rail-title h1 {
    margin: 0;
    font-size: 1.4rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--hk-green) !important;
    text-shadow: 0 0 8px var(--hk-green-glow), 0 0 18px var(--hk-green-glow);
}
#rail-title h1::after {
    content: "_";
    color: var(--hk-border-bright);
    animation: hk-blink 1s steps(1) infinite;
}
#rail-title p {
    margin: 0.15rem 0 0;
    font-size: 0.82rem;
    color: var(--hk-green-dim) !important;
    letter-spacing: 0.02em;
}
#new-session-btn {
    width: 100% !important;
    font-size: 1rem !important;
    padding: 10px !important;
    margin-bottom: 0.2rem;
}

/* ── Sessions list (styled as clickable rows, not radio buttons) ── */
#sessions-list { max-height: 42vh; overflow-y: auto; padding-right: 2px; }
#sessions-list .wrap { display: flex; flex-direction: column; gap: 6px; }
#sessions-list label {
    display: flex !important;
    align-items: center;
    width: 100%;
    padding: 8px 10px !important;
    margin: 0 !important;
    border-radius: 3px !important;
    cursor: pointer;
    font-size: 0.85rem !important;
    line-height: 1.35;
    white-space: normal;
    word-break: break-word;
}
#sessions-list label:hover {
    border-color: var(--hk-border-bright) !important;
    box-shadow: 0 0 8px var(--hk-green-glow);
}
#sessions-list label:has(input:checked) {
    background: rgba(57,255,106,0.12) !important;
    border-left: 3px solid var(--hk-border-bright) !important;
    color: var(--hk-green) !important;
}
#sessions-list input[type="radio"] {
    accent-color: var(--hk-green);
    margin-right: 8px;
    flex-shrink: 0;
}

/* ── Top bar (in the chat column) ──────────────────────── */
#top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.9rem 0 0.75rem;
    border-bottom: 1px solid var(--hk-border);
    margin-bottom: 0.85rem;
    gap: 1rem;
    background: transparent !important;
}
#top-bar-title {
    font-size: 0.85rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--hk-green-dim) !important;
}
#top-bar-title p { margin: 0; }

/* ── Connection badge ─────────────────────────────────── */
#connection-badge {
    font-size: 0.95rem !important;
    padding: 0.32rem 0.8rem !important;
    border-radius: 2px !important;
    background: #000 !important;
    border: 1px solid var(--hk-border) !important;
    white-space: nowrap;
    min-height: unset !important;
}
#connection-badge p { margin: 0; }

/* ── Model selector row ───────────────────────────────── */
#model-row { margin-bottom: 0.35rem; }
#status-note {
    font-size: 0.95rem;
    color: var(--hk-green-dim) !important;
    min-height: 1rem;
    margin: 0 0 0.6rem;
    padding: 0 !important;
}
#status-note p { margin: 0; }
#status-note p::before { content: "$ "; color: var(--hk-border-bright); }

/* ── Chat panel ───────────────────────────────────────── */
#chat-panel {
    border-radius: 4px !important;
    border: 1px solid var(--hk-border) !important;
    box-shadow: 0 0 24px rgba(57,255,106,0.08), inset 0 0 40px rgba(0,0,0,0.6) !important;
    overflow: hidden;
    background: #030503 !important;
}
#chat-panel .bot .message-content {
    border-left: 3px solid var(--hk-green) !important;
    background: rgba(57,255,106,0.05) !important;
}
#chat-panel .user .message-content {
    border-left: 3px solid var(--hk-cyan) !important;
    background: rgba(77,250,255,0.05) !important;
}

/* ── Metrics ──────────────────────────────────────────── */
#conv-bar, #metrics-bar {
    font-size: 0.92rem !important;
    padding: 0.42rem 0.9rem !important;
    border-radius: 2px !important;
    margin-top: 0.3rem !important;
    min-height: unset !important;
}
#conv-bar {
    background: rgba(57,255,106,0.05) !important;
    border: 1px solid var(--hk-border) !important;
    color: var(--hk-green) !important;
}
#conv-bar p { margin: 0; }
#metrics-bar {
    background: #000 !important;
    border: 1px solid var(--hk-border) !important;
    color: var(--hk-green-dim) !important;
}
#metrics-bar p { margin: 0; }

/* ── Debug panel ──────────────────────────────────────── */
#debug-panel {
    border-radius: 2px !important;
    padding: 0.75rem 0.9rem !important;
    margin-top: 0.3rem !important;
    font-size: 0.92rem !important;
    line-height: 1.5;
    font-family: var(--hk-mono) !important;
    background: #000 !important;
    color: var(--hk-green) !important;
    border: 1px solid var(--hk-border) !important;
    max-height: 360px;
    overflow-y: auto;
}

/* ── Input row ────────────────────────────────────────── */
#input-row {
    margin-top: 0.5rem !important;
    align-items: flex-end !important;
}
#input-row textarea {
    border-radius: 4px !important;
    resize: none !important;
}
#send-btn, #stop-btn {
    border-radius: 4px !important;
    min-height: 42px !important;
}

/* ── Footer actions ───────────────────────────────────── */
#footer-row {
    margin-top: 0.4rem !important;
    gap: 0.4rem !important;
}
#footer-row button {
    font-size: 0.9rem !important;
    padding: 7px 13px !important;
    border-radius: 4px !important;
    min-width: 0 !important;
}

/* ── Sidebar ──────────────────────────────────────────── */
#sidebar {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
#sidebar .gr-accordion {
    border-radius: 4px !important;
    border: 1px solid var(--hk-border) !important;
    overflow: hidden;
}

/* ── Info panels (model info / ollama info) ───────────── */
#info-panel {
    border-radius: 2px !important;
    padding: 0.7rem 0.9rem !important;
    margin-top: 0.5rem !important;
    font-size: 0.95rem !important;
    line-height: 1.5;
    background: #000 !important;
    color: var(--hk-green) !important;
    border: 1px solid var(--hk-border) !important;
    max-height: 420px;
    overflow-y: auto;
}

/* ── Attachments ──────────────────────────────────────── */
#attach-summary {
    font-size: 0.9rem !important;
    color: var(--hk-green-dim) !important;
    min-height: unset !important;
    padding: 0 !important;
}
#attach-summary p { margin: 0; }

/* ── Sidebar section labels ───────────────────────────── */
.sidebar-note {
    font-size: 0.9rem;
    color: var(--hk-green-dim) !important;
    margin: 0 0 0.4rem !important;
    padding: 0 !important;
    line-height: 1.5;
}
.sidebar-note p { margin: 0; }
/* ── Mobile: stack rows, bigger touch targets, no horizontal scroll ── */
@media (max-width: 640px) {
    html, body, gradio-app, .gradio-container { overflow-x: hidden !important; max-width: 100vw !important; }
    .gradio-container { width: 100vw !important; padding: 0 0.5rem !important; }
    #app-shell, #model-row, #input-row, #footer-row, #top-bar,
    #sidebar .row, #sessions-rail .row {
        flex-direction: column !important;
        flex-wrap: nowrap !important;
    }
    #app-shell > *, #model-row > *, #input-row > *, #footer-row > * {
        width: 100% !important;
        min-width: 0 !important;
        flex: 1 1 auto !important;
    }
    #sessions-rail, #sidebar, #main-col { min-width: 0 !important; width: 100% !important; }
    #send-col { flex-direction: row !important; gap: 0.5rem !important; }
    #send-col > * { flex: 1 1 0 !important; }
    button, .gr-button, [role="button"] { min-height: 44px !important; }
    #chat-panel .bubble-wrap, #chat-panel > div { height: 50vh !important; min-height: 280px !important; }
    #connection-badge, #status-note, #conv-bar, #metrics-bar, #debug-panel, #info-panel, .sidebar-note {
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
    }
}
"""

APP_THEME = gr.themes.Base(
    primary_hue="green",
    secondary_hue="green",
    neutral_hue="gray",
    text_size=gr.themes.sizes.text_lg,  # bump the whole text scale up a notch — defaults run small
    font=[gr.themes.GoogleFont("Share Tech Mono"), "ui-monospace", "monospace"],
    font_mono=[gr.themes.GoogleFont("Share Tech Mono"), "ui-monospace", "monospace"],
).set(
    button_large_padding="10px 20px",
    button_small_padding="7px 13px",
    block_label_margin="4px",
    block_title_text_weight="600",
    section_header_text_weight="600",
    chatbot_text_size="18px",
    # ── Force a single dark hacker palette regardless of client light/dark pref ──
    body_background_fill="#060a06",
    body_background_fill_dark="#060a06",
    background_fill_primary="#0a0f0a",
    background_fill_primary_dark="#0a0f0a",
    background_fill_secondary="#0d140d",
    background_fill_secondary_dark="#0d140d",
    body_text_color="#39ff6a",
    body_text_color_dark="#39ff6a",
    body_text_color_subdued="#1f8f45",
    body_text_color_subdued_dark="#1f8f45",
    border_color_primary="#1d5c33",
    border_color_primary_dark="#1d5c33",
    border_color_accent="#2fdc6c",
    border_color_accent_dark="#2fdc6c",
    block_background_fill="#0a0f0a",
    block_background_fill_dark="#0a0f0a",
    block_border_color="#1d5c33",
    block_border_color_dark="#1d5c33",
    block_label_background_fill="#000000",
    block_label_background_fill_dark="#000000",
    block_label_text_color="#39ff6a",
    block_label_text_color_dark="#39ff6a",
    block_title_text_color="#39ff6a",
    block_title_text_color_dark="#39ff6a",
    panel_background_fill="#0a0f0a",
    panel_background_fill_dark="#0a0f0a",
    panel_border_color="#1d5c33",
    panel_border_color_dark="#1d5c33",
    input_background_fill="#000000",
    input_background_fill_dark="#000000",
    input_border_color="#1d5c33",
    input_border_color_dark="#1d5c33",
    input_border_color_focus="#2fdc6c",
    input_border_color_focus_dark="#2fdc6c",
    input_placeholder_color="#1f8f45",
    input_placeholder_color_dark="#1f8f45",
    code_background_fill="#000000",
    code_background_fill_dark="#000000",
    button_primary_background_fill="#000000",
    button_primary_background_fill_dark="#000000",
    button_primary_background_fill_hover="#39ff6a",
    button_primary_background_fill_hover_dark="#39ff6a",
    button_primary_text_color="#39ff6a",
    button_primary_text_color_dark="#39ff6a",
    button_primary_text_color_hover="#000000",
    button_primary_text_color_hover_dark="#000000",
    button_primary_border_color="#2fdc6c",
    button_primary_border_color_dark="#2fdc6c",
    button_secondary_background_fill="#000000",
    button_secondary_background_fill_dark="#000000",
    button_secondary_background_fill_hover="#39ff6a",
    button_secondary_background_fill_hover_dark="#39ff6a",
    button_secondary_text_color="#39ff6a",
    button_secondary_text_color_dark="#39ff6a",
    button_secondary_text_color_hover="#000000",
    button_secondary_text_color_hover_dark="#000000",
    button_secondary_border_color="#1d5c33",
    button_secondary_border_color_dark="#1d5c33",
    button_cancel_background_fill="#000000",
    button_cancel_background_fill_dark="#000000",
    button_cancel_text_color="#ff3b57",
    button_cancel_text_color_dark="#ff3b57",
    button_cancel_border_color="#ff3b57",
    button_cancel_border_color_dark="#ff3b57",
    slider_color="#39ff6a",
    slider_color_dark="#39ff6a",
    checkbox_background_color="#000000",
    checkbox_background_color_dark="#000000",
    checkbox_background_color_selected="#39ff6a",
    checkbox_background_color_selected_dark="#39ff6a",
    checkbox_border_color="#1d5c33",
    checkbox_border_color_dark="#1d5c33",
    checkbox_border_color_selected="#2fdc6c",
    checkbox_border_color_selected_dark="#2fdc6c",
    checkbox_label_background_fill="#000000",
    checkbox_label_background_fill_dark="#000000",
    checkbox_label_text_color="#39ff6a",
    checkbox_label_text_color_dark="#39ff6a",
    error_background_fill="#1a0507",
    error_background_fill_dark="#1a0507",
    error_border_color="#ff3b57",
    error_border_color_dark="#ff3b57",
    error_text_color="#ff3b57",
    error_text_color_dark="#ff3b57",
    link_text_color="#4dfaff",
    link_text_color_dark="#4dfaff",
    link_text_color_hover="#39ff6a",
    link_text_color_hover_dark="#39ff6a",
)


SETTINGS_KEY = "toty-webui.settings.v1"

# Browser-side persistence (localStorage). Only host/port are stored — never secrets.
JS_LOAD_SETTINGS = """
(host, port) => {
  try {
    const raw = window.localStorage.getItem("%(key)s");
    if (raw) {
      const s = JSON.parse(raw);
      if (s && typeof s.host === "string" && Number.isInteger(s.port)) return [s.host, s.port];
    }
  } catch (e) {}
  return [host, port];
}
""" % {"key": SETTINGS_KEY}

JS_STORE_SETTINGS = """
(host, port, ok) => {
  if (ok === "1") {
    try {
      window.localStorage.setItem("%(key)s", JSON.stringify({v: 1, host: host, port: Number(port)}));
    } catch (e) {}
  }
}
""" % {"key": SETTINGS_KEY}

JS_FORGET_SETTINGS = """
() => { try { window.localStorage.removeItem("%(key)s"); } catch (e) {} }
""" % {"key": SETTINGS_KEY}


def validate_settings(host, port):
    """Validate connection fields before they are saved to browser storage."""
    _, err = parse_connection(host, port)
    if err:
        return f"Not saved: {err}", "0"
    return "Saved locally in this browser.", "1"


def reset_settings():
    return env_host(), int(env_port()), "Reset to defaults; saved settings removed."


def forget_settings():
    return "Saved settings removed; current values kept until reload."


def build_ui() -> gr.Blocks:
    host_default = env_host()
    port_default = env_port()

    with gr.Blocks(title=APP_TITLE) as demo:

        # ── Three-column shell: sessions | chat | settings ─────
        with gr.Row(equal_height=False, elem_id="app-shell"):

            # ── Left: sessions rail ─────────────────────────────
            with gr.Column(scale=2, min_width=230, elem_id="sessions-rail"):
                gr.HTML(
                    f"""
                    <div id="rail-title">
                        <h1>root@{APP_TITLE.lower()}:~</h1>
                        <p>// local llm uplink</p>
                    </div>
                    """
                )
                btn_new = gr.Button("🆕  New conversation", variant="primary", elem_id="new-session-btn")
                btn_save = gr.Button("💾  Save to SQLite", variant="secondary")

                gr.Markdown("**Recent sessions**", elem_classes="sidebar-note")
                sessions_radio = gr.Radio(
                    show_label=False,
                    choices=[],
                    value=None,
                    elem_id="sessions-list",
                )
                btn_refresh_sessions = gr.Button("↺  Refresh list", variant="secondary")

                with gr.Row():
                    btn_delete_session = gr.Button("🗑 Delete", variant="secondary")
                    btn_export_session = gr.Button("⇩ Export", variant="secondary")

                gr.Markdown("---")
                btn_export_txt = gr.Button("⇩ Export current chat", variant="secondary")
                export_file = gr.File(label="Download", visible=False, interactive=False)

                gr.Markdown("---")
                wipe_confirm_chk = gr.Checkbox(
                    label="Confirm — permanently delete ALL saved conversations",
                    value=False,
                )
                btn_wipe = gr.Button("⚠ Wipe history database", variant="stop")
                history_status = gr.Markdown(value="", elem_classes="sidebar-note")

            # ── Center: chat ─────────────────────────────────────
            with gr.Column(scale=7, min_width=0, elem_id="main-col"):

                with gr.Row(elem_id="top-bar"):
                    gr.Markdown("**Conversation**", elem_id="top-bar-title")
                    connection_badge = gr.Markdown(
                        value="⚪ **Starting…**",
                        elem_id="connection-badge",
                    )

                with gr.Row(elem_id="model-row"):
                    model_dd = gr.Dropdown(
                        label="Model",
                        choices=[],
                        allow_custom_value=True,
                        scale=5,
                        info="Select a pulled model or type a custom name.",
                    )
                    btn_refresh = gr.Button("↺  Refresh", variant="primary", scale=1, min_width=110)

                status = gr.Markdown(value="", elem_id="status-note")

                with gr.Group(elem_id="chat-panel"):
                    chat = gr.Chatbot(
                        label="Conversation",
                        show_label=False,
                        height=600,
                        buttons=["copy", "copy_all"],
                    )

                metrics_empty, debug_empty = empty_metrics_display()
                conv_bar = gr.Markdown(value="", elem_id="conv-bar")
                metrics_bar = gr.Markdown(value=metrics_empty, elem_id="metrics-bar")
                debug_panel = gr.Markdown(
                    value=debug_empty,
                    elem_id="debug-panel",
                    visible=env_debug_mode(),
                )

                with gr.Row(elem_id="input-row"):
                    msg = gr.Textbox(
                        placeholder="Message… (Enter to send · Shift+Enter for new line)",
                        lines=1,
                        max_lines=6,
                        scale=5,
                        show_label=False,
                        container=False,
                    )
                    with gr.Column(scale=1, min_width=90, elem_id="send-col"):
                        submit_btn = gr.Button("Send ▶", variant="primary", elem_id="send-btn")
                        btn_stop = gr.Button("■ Stop", variant="stop", elem_id="stop-btn")

                with gr.Row(elem_id="footer-row"):
                    btn_clear = gr.Button("🗑 Clear", variant="secondary")
                    btn_clear_files = gr.Button("📎 Clear files", variant="secondary")
                    btn_example = gr.Button("💡 Example", variant="secondary")

            # ── Right: settings sidebar ────────────────────────
            with gr.Column(scale=2, min_width=250, elem_id="sidebar"):

                with gr.Accordion("🔌  Connection", open=False):
                    with gr.Row():
                        host_in = gr.Textbox(
                            label="Host",
                            value=host_default,
                            placeholder=DEFAULT_HOST,
                            scale=3,
                        )
                        port_in = gr.Number(
                            label="Port",
                            value=int(port_default),
                            precision=0,
                            minimum=1,
                            maximum=65535,
                            scale=1,
                        )
                    with gr.Row():
                        btn_save_settings = gr.Button("Save settings", variant="secondary")
                        btn_reset_settings = gr.Button("Reset", variant="secondary")
                        btn_forget_settings = gr.Button("Forget", variant="secondary")
                    settings_status = gr.Markdown(value="", elem_classes="sidebar-note")
                    settings_ok = gr.Textbox(value="0", visible=False)
                    with gr.Row():
                        btn_ping = gr.Button("Ping", variant="secondary")
                        btn_ollama_info = gr.Button("Server info", variant="secondary")
                    ollama_info_panel = gr.Markdown(value="", elem_id="info-panel", visible=False)

                with gr.Accordion("🗂  Memory", open=False):
                    gr.Markdown(
                        "Models stay loaded in GPU/RAM after use.",
                        elem_classes="sidebar-note",
                    )
                    with gr.Row():
                        btn_loaded = gr.Button("Show loaded", variant="secondary")
                        btn_unload = gr.Button("Unload model", variant="secondary")
                    btn_unload_all = gr.Button("Unload all", variant="secondary")
                    gr.Markdown("---")
                    gr.Markdown(
                        "Inspect context window, quant, and architecture.",
                        elem_classes="sidebar-note",
                    )
                    btn_model_info = gr.Button("Model info", variant="secondary")
                    model_info_panel = gr.Markdown(value="", elem_id="info-panel", visible=False)

                with gr.Accordion("⚙️  Generation", open=False):
                    system_prompt = gr.Textbox(
                        label="System prompt",
                        lines=3,
                        placeholder="You are a helpful assistant…",
                    )
                    temperature = gr.Slider(
                        label="Temperature",
                        minimum=0.0,
                        maximum=2.0,
                        value=0.7,
                        step=0.05,
                    )
                    num_ctx = gr.Number(
                        label="Context window (num_ctx)",
                        value=0,
                        precision=0,
                        minimum=0,
                        maximum=1048576,
                        info="0 = modelfile default (Ollama falls back to 2,048 if unset).",
                    )
                    think_mode = gr.Radio(
                        label="Thinking mode",
                        choices=["Off", "Low", "Medium", "High"],
                        value="Off",
                        info="Reasoning models only (gpt-oss, deepseek-r1, qwq…) — shown as a "
                        "collapsible '🤔 Thought' bubble above the reply. Picking Low/Medium/High "
                        "on a model with no thinking support returns an error. Some models (e.g. "
                        "gpt-oss) always reason a little even when this is set to Off.",
                    )
                    stream_delay = gr.Slider(
                        label="Stream delay (ms / chunk)",
                        minimum=0,
                        maximum=200,
                        value=0,
                        step=5,
                        info="Typewriter effect. 0 = full speed.",
                    )
                    debug_mode = gr.Checkbox(
                        label="Debug mode",
                        value=env_debug_mode(),
                        info="Show timing breakdown and raw Ollama stats after each reply.",
                    )

                with gr.Accordion("📎  Attachments", open=False):
                    files = gr.File(
                        label="Attach files (text, code, PDF)",
                        file_count="multiple",
                        type="filepath",
                    )
                    attachment_summary = gr.Markdown(
                        value="_No files attached._",
                        elem_id="attach-summary",
                    )

                with gr.Accordion("🔍  Web Search", open=False):
                    search_enabled_chk = gr.Checkbox(
                        label="Enable web search",
                        value=False,
                        info="Off by default. Searches DuckDuckGo using your message as the "
                        "query and feeds the results to the model for that one reply. "
                        "Results are never saved, exported, or shown in the transcript.",
                    )
                    search_max_results = gr.Slider(
                        label="Max results",
                        minimum=1,
                        maximum=10,
                        value=4,
                        step=1,
                    )

        # ── State ──────────────────────────────────────────────
        stop_state = gr.State(lambda: {"abort": False})
        conv_state = gr.State(lambda: {"prompt": 0, "completion": 0, "turns": 0})
        conv_id_state = gr.State(lambda: None)  # DB row id of the current conversation, if saved

        # ── Event helpers ──────────────────────────────────────
        def on_refresh(h, p, current_model):
            choices, note, badge = fetch_models(h, p, current_model)
            val = pick_model_value(choices, current_model)
            return gr.update(choices=choices, value=val), note, badge

        def on_ping(h, p):
            note, badge = ping_host(h, p)
            return note, badge

        def on_files_change(uploads):
            return summarize_upload_paths(uploads)

        def clear_uploads():
            return gr.update(value=None), summarize_upload_paths(None)

        def run_chat(
            message, hist, h, p, mdl, sys_p, temp, ctx, delay, uploads, flag, cs, debug, search_on, search_n, think
        ):
            yield from stream_reply(
                message, hist, h, p, mdl, sys_p, temp, ctx, delay, uploads, flag, cs, debug,
                search_on, search_n, think,
            )

        def toggle_debug_panel(enabled: bool):
            return gr.update(visible=bool(enabled))

        def on_new_conversation(stop_flag):
            result = clear_chat(stop_flag)
            return (*result, None)  # also reset conv_id_state — starts a fresh conversation

        def on_save_conversation(hist, mdl, conv_id):
            if not hist:
                return conv_id, gr.update(), "_Nothing to save — chat is empty._"
            new_id = history_store.save_conversation(conv_id, mdl, hist)
            return new_id, gr.update(choices=history_store.list_conversation_choices()), f"Saved conversation #{new_id}."

        def on_export_txt(hist, mdl):
            if not hist:
                return gr.update(visible=False), "_Nothing to export — chat is empty._"
            path = history_store.export_to_txt(hist, mdl)
            return gr.update(value=path, visible=True), "Exported current chat."

        def on_refresh_sessions():
            return gr.update(choices=history_store.list_conversation_choices())

        def on_open_session(selected_id):
            if selected_id is None:
                # Radio gets reset to None programmatically after delete/wipe — no-op rather
                # than clobbering whatever status message that action just set.
                return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
            messages = history_store.load_conversation(selected_id)
            empty_conv = {"prompt": 0, "completion": 0, "turns": 0}
            return messages, selected_id, empty_conv, "", f"Opened conversation #{selected_id}."

        def on_delete_session(selected_id, conv_id):
            if selected_id is None:
                return gr.update(), conv_id, "_Pick a conversation first._"
            history_store.delete_conversation(selected_id)
            new_conv_id = None if conv_id == selected_id else conv_id
            return (
                gr.update(choices=history_store.list_conversation_choices(), value=None),
                new_conv_id,
                f"Deleted conversation #{selected_id}.",
            )

        def on_export_session(selected_id):
            if selected_id is None:
                return gr.update(visible=False), "_Pick a conversation first._"
            path = history_store.export_conversation_to_txt(selected_id)
            if not path:
                return gr.update(visible=False), "_Conversation not found — refresh the list._"
            return gr.update(value=path, visible=True), f"Exported conversation #{selected_id}."

        def on_wipe(confirmed, conv_id):
            if not confirmed:
                return gr.update(), conv_id, gr.update(), "_Check the confirm box first — nothing was deleted._"
            n = history_store.wipe_all()
            note = f"Deleted **{n}** saved conversation(s)." if n else "Database was already empty."
            return False, None, gr.update(choices=[], value=None), note

        # ── Event wiring ───────────────────────────────────────
        debug_mode.change(toggle_debug_panel, inputs=[debug_mode], outputs=[debug_panel])

        btn_refresh.click(on_refresh, [host_in, port_in, model_dd], [model_dd, status, connection_badge])
        btn_ping.click(on_ping, [host_in, port_in], [status, connection_badge])
        btn_loaded.click(list_loaded_models, [host_in, port_in], [status])
        btn_unload.click(unload_model, [host_in, port_in, model_dd], [status])
        btn_unload_all.click(unload_all_loaded_models, [host_in, port_in], [status])
        btn_ollama_info.click(
            lambda h, p: gr.update(value=ollama_server_info(h, p), visible=True),
            inputs=[host_in, port_in],
            outputs=[ollama_info_panel],
        )
        btn_model_info.click(
            lambda h, p, m: gr.update(value=show_model_info(h, p, m), visible=True),
            inputs=[host_in, port_in, model_dd],
            outputs=[model_info_panel],
        )
        btn_clear.click(
            on_new_conversation,
            inputs=[stop_state],
            outputs=[chat, msg, stop_state, metrics_bar, debug_panel, conv_state, conv_bar, conv_id_state],
        )
        btn_stop.click(request_stop, inputs=[stop_state], outputs=[stop_state])
        btn_clear_files.click(clear_uploads, outputs=[files, attachment_summary])
        btn_example.click(insert_example_prompt, outputs=[msg])
        files.change(on_files_change, inputs=[files], outputs=[attachment_summary])

        btn_new.click(
            on_new_conversation,
            inputs=[stop_state],
            outputs=[chat, msg, stop_state, metrics_bar, debug_panel, conv_state, conv_bar, conv_id_state],
        )
        btn_save.click(
            on_save_conversation,
            inputs=[chat, model_dd, conv_id_state],
            outputs=[conv_id_state, sessions_radio, history_status],
        )
        btn_refresh_sessions.click(on_refresh_sessions, outputs=[sessions_radio])
        sessions_radio.change(
            on_open_session,
            inputs=[sessions_radio],
            outputs=[chat, conv_id_state, conv_state, conv_bar, history_status],
        )
        btn_delete_session.click(
            on_delete_session,
            inputs=[sessions_radio, conv_id_state],
            outputs=[sessions_radio, conv_id_state, history_status],
        )
        btn_export_session.click(on_export_session, inputs=[sessions_radio], outputs=[export_file, history_status])
        btn_export_txt.click(on_export_txt, inputs=[chat, model_dd], outputs=[export_file, history_status])
        btn_wipe.click(
            on_wipe,
            inputs=[wipe_confirm_chk, conv_id_state],
            outputs=[wipe_confirm_chk, conv_id_state, sessions_radio, history_status],
        )

        chat_inputs = [
            msg, chat,
            host_in, port_in, model_dd,
            system_prompt, temperature, num_ctx, stream_delay,
            files, stop_state, conv_state, debug_mode,
            search_enabled_chk, search_max_results, think_mode,
        ]
        chat_outputs = [chat, msg, metrics_bar, debug_panel, conv_state, conv_bar]

        msg.submit(run_chat, chat_inputs, chat_outputs)
        submit_btn.click(run_chat, chat_inputs, chat_outputs)

        btn_save_settings.click(
            validate_settings, [host_in, port_in], [settings_status, settings_ok]
        ).then(None, [host_in, port_in, settings_ok], None, js=JS_STORE_SETTINGS)
        btn_reset_settings.click(
            reset_settings, None, [host_in, port_in, settings_status]
        ).then(None, None, None, js=JS_FORGET_SETTINGS)
        btn_forget_settings.click(forget_settings, None, [settings_status]).then(
            None, None, None, js=JS_FORGET_SETTINGS
        )

        # Restore saved host/port first so the initial model refresh uses them.
        demo.load(None, [host_in, port_in], [host_in, port_in], js=JS_LOAD_SETTINGS).then(
            on_refresh, [host_in, port_in, model_dd], [model_dd, status, connection_badge]
        )
        demo.load(on_refresh_sessions, outputs=[sessions_radio])

    return demo


def main() -> None:
    server = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    port = gradio_server_port()
    share = os.environ.get("GRADIO_SHARE", "").lower() in ("1", "true", "yes")
    ssl_kwargs = ssl_launch_kwargs()

    demo = build_ui()
    log_port = port if port is not None else "7860+ (first free)"
    logger.info("Starting Gradio on %s:%s (Ollama default host %s)", server, log_port, DEFAULT_HOST)

    if ssl_kwargs:
        logger.info("HTTPS enabled (cert.pem + key.pem found in project root)")
    else:
        logger.info(
            "HTTP mode — place cert.pem and key.pem in the project root for HTTPS "
            "(see README: openssl req -x509 …)"
        )

    demo.launch(
        server_name=server,
        server_port=port,
        share=share,
        theme=APP_THEME,
        css=CUSTOM_CSS,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
