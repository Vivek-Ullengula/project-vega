"""
Coaction Binding Authority Assistant — Gradio 6.5 UI
Minimalist monochrome design with real-time streaming.
"""

import gradio as gr
import requests
import os
import uuid
from datetime import datetime

# Optional: Import the FastAPI app for unified deployment
try:
    from app.main import app as fastapi_app
except ImportError:
    fastapi_app = None

# Unified Deployment: If we are running in the same process, point to localhost or relative path
API_BASE = os.getenv("API_BASE_URL", "/v1" if fastapi_app else "http://localhost:8000/v1")
ALLOWED_ROLES = ("agent", "underwriter", "external")

# ─── Helpers ─────────────────────────────────────────────────────────────────


def new_session_id() -> str:
    return str(uuid.uuid4())


def get_headers(token: str):
    """Return headers with both standard and AgentCore-specific custom auth."""
    auth_val = f"Bearer {token}"
    return {
        "Authorization": auth_val,
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization": auth_val,
    }


def signup_user(name: str, email: str, password: str, role: str):
    try:
        r = requests.post(
            f"{API_BASE}/auth/signup",
            json={
                "name": (name or "").strip(),
                "email": (email or "").strip(),
                "password": password or "",
                "role": (role or "").strip().lower(),
            },
            timeout=10,
        )
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text)
            return f"Signup failed: {detail}"
        return "Signup successful. Please login."
    except Exception as exc:
        return f"Signup failed: {exc}"


def verify_user(email: str, code: str):
    if not email or not code:
        return "⚠️ Email and verification code are required."
    try:
        r = requests.post(
            f"{API_BASE}/auth/confirm",
            json={"email": email.strip(), "confirmation_code": code.strip()},
            timeout=10,
        )
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text)
            return f"❌ Verification failed: {detail}"
        return "✅ Verification successful! You can now switch to the Login tab."
    except Exception as exc:
        return f"❌ Verification failed: {exc}"


def login_user(email: str, password: str):
    if not email or not password:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            "Please enter both email and password.",
            gr.update(visible=False),
            gr.update(visible=True),
            "",
            gr.update(choices=[]),
            gr.update(visible=False),
        )
    try:
        r = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": (email or "").strip(), "password": password or ""},
            timeout=10,
        )
        if r.status_code >= 400:
            if r.status_code == 401:
                detail = "Invalid credentials"
            else:
                detail = r.json().get("detail", r.text)
            return (
                {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
                f"Login failed: {detail}",
                gr.update(visible=False),
                gr.update(visible=True),
                "",
                gr.update(choices=[]),
            )
        payload = r.json()
        user = payload.get("user", {})
        token = payload.get("access_token", "")
        session_user = {
            "authenticated": True,
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "role": user.get("role", ""),
            "token": token,
        }
        role_key = str(session_user.get("role", "")).strip().lower()
        user_name = session_user["name"]
        if role_key == "underwriter":
            welcome = f"Welcome to the Underwriter Portal, {user_name}."
        elif role_key == "agent":
            welcome = f"Welcome to the Agent Portal, {user_name}."
        else:
            welcome = f"Welcome, {user_name}."
        # Fetch user's session history directly
        dropdown_choices = []
        try:
            sessions_resp = requests.get(f"{API_BASE}/sessions", headers=get_headers(token))
            if sessions_resp.ok:
                sessions = sessions_resp.json()
                for s in sessions:
                    dt_str = s.get("last_accessed", "")
                    title = s.get("title", "New Chat")
                    try:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        date_fmt = dt.strftime("%Y-%m-%d %H:%M")
                        display_text = f"[{date_fmt}] {title}"
                    except Exception:
                        display_text = title
                    dropdown_choices.append((display_text, s["session_id"]))
        except Exception as e:
            print(f"Failed to fetch sessions: {e}")

        is_underwriter = role_key == "underwriter"
        return (
            session_user,
            welcome,
            gr.update(visible=True),
            gr.update(visible=False),
            welcome,
            gr.update(choices=dropdown_choices),
            gr.update(visible=is_underwriter),
        )
    except Exception as exc:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            f"Login failed: {exc}",
            gr.update(visible=False),
            gr.update(visible=True),
            "",
            gr.update(choices=[]),
            gr.update(visible=False),
        )


def logout_user():
    return (
        {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
        "Logged out.",
        gr.update(visible=False),
        gr.update(visible=True),
        "",
        [],  # chatbot
        "",  # session_state
        gr.update(value="", visible=False),  # fu1
        gr.update(value="", visible=False),  # fu2
        gr.update(value="", visible=False),  # fu3
        gr.update(visible=True),  # suggestions
        "",  # msg
        gr.update(choices=[]),  # history_dropdown
        gr.update(visible=False),  # kb_accordion
    )


def refresh_dropdown(user_state):
    choices = []
    if user_state and user_state.get("token"):
        try:
            resp = requests.get(
                f"{API_BASE}/sessions", headers=get_headers(user_state.get("token"))
            )
            if resp.ok:
                sessions = resp.json()
                for s in sessions:
                    dt_str = s.get("last_accessed", "")
                    title = s.get("title", "New Chat")
                    try:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        date_fmt = dt.strftime("%Y-%m-%d %H:%M")
                        display_text = f"[{date_fmt}] {title}"
                    except Exception:
                        display_text = title
                    choices.append((display_text, s["session_id"]))
        except Exception:
            pass
    return gr.update(choices=choices)


def load_session(session_id, user_state):
    hide_btn = gr.update(visible=False)
    if not session_id or not user_state:
        return [], session_id, hide_btn, hide_btn, hide_btn, hide_btn
    try:
        resp = requests.get(
            f"{API_BASE}/sessions/{session_id}", headers=get_headers(user_state.get("token"))
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
        return messages, session_id, hide_btn, hide_btn, hide_btn, hide_btn
    except Exception as e:
        print(f"Failed to load session: {e}")
        return [], session_id, hide_btn, hide_btn, hide_btn, hide_btn


def create_kb(name, desc, bucket, prefix, user_state):
    if not user_state or not user_state.get("authenticated"):
        return "⚠️ Please login first."
    if not name or not bucket:
        return "⚠️ Name and S3 Bucket are required."

    try:
        r = requests.post(
            f"{API_BASE}/knowledge-bases",
            json={"name": name, "description": desc, "s3_bucket": bucket, "s3_prefix": prefix},
            headers=get_headers(user_state.get("token", "")),
            timeout=60,
        )
        if not r.ok:
            try:
                err_msg = r.json().get("detail", r.text)
            except Exception:
                err_msg = r.text
            return f"❌ Failed to create KB: {err_msg}"

        data = r.json()
        kb_id = data.get("kb_id", "")
        return (
            f"✅ Knowledge Base '{name}' created successfully! (ID: {kb_id}). Sync is in progress."
        )
    except Exception as exc:
        return f"❌ Error: {exc}"


def api_health() -> str:
    try:
        r = requests.get(API_BASE.replace("/api/v1", "/health"), timeout=2)
        return "🟢 Online" if r.ok else "🟡 Degraded"
    except Exception:
        return "🔴 Offline"


# ─── Theme ───────────────────────────────────────────────────────────────────

THEME = gr.themes.Soft(
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
    primary_hue="blue",
    neutral_hue="slate",
    radius_size=gr.themes.sizes.radius_md,
).set(
    body_background_fill="*neutral_50",
    block_background_fill="white",
    block_border_width="0px",
    block_label_background_fill="*primary_100",
    button_primary_background_fill="linear-gradient(135deg, *primary_600, *primary_500)",
    button_primary_background_fill_hover="linear-gradient(135deg, *primary_500, *primary_400)",
    button_primary_text_color="white",
    button_secondary_background_fill="white",
    button_secondary_border_color="*neutral_200",
    button_secondary_text_color="*neutral_700",
    border_color_primary="*neutral_200",
    color_accent_soft="*primary_50",
    panel_background_fill="white",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
/* Ultra-Premium Glassmorphism UI */
body, .gradio-container {
    background: linear-gradient(135deg, #f6f8fd, #f1f5f9) !important;
}

/* Sidebar Styling */
.sidebar {
    background: rgba(255, 255, 255, 0.7) !important;
    backdrop-filter: blur(12px) !important;
    border-right: 1px solid rgba(255, 255, 255, 0.5) !important;
}

/* Chatbot container */
#chatbot { 
    height: 680px !important; 
    border: 1px solid rgba(255, 255, 255, 0.6) !important;
    background: rgba(255, 255, 255, 0.4) !important;
    backdrop-filter: blur(16px) !important;
    border-radius: 24px !important;
    box-shadow: 0 10px 40px -10px rgba(0,0,0,0.05) !important;
    padding: 15px !important;
}

/* Message Bubbles (Handles both Gradio versions) */
.message-row.user .message, #chatbot .message.user {
    background: linear-gradient(135deg, #4f46e5, #3b82f6) !important;
    color: white !important;
    border-radius: 20px 20px 4px 20px !important;
    padding: 14px 20px !important;
    box-shadow: 0 8px 16px -4px rgba(59, 130, 246, 0.3) !important;
    border: none !important;
}
.message-row.user .message *, #chatbot .message.user * { color: white !important; }

.message-row.bot .message, #chatbot .message.bot {
    background: #ffffff !important;
    color: #1e293b !important;
    border-radius: 20px 20px 20px 4px !important;
    padding: 14px 20px !important;
    box-shadow: 0 4px 12px -2px rgba(0, 0, 0, 0.05) !important;
    border: 1px solid #f1f5f9 !important;
}

/* Smaller text in messages */
.message-wrap { 
    font-size: 0.95rem !important; 
    line-height: 1.65 !important; 
    letter-spacing: -0.01em !important;
}

/* Follow-up row buttons (Chips) */
.fu-row {
    margin-top: 10px !important;
    gap: 8px !important;
}
.fu-row button { 
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    color: #334155 !important;
    border-radius: 100px !important;
    font-size: 0.82rem !important; 
    padding: 8px 18px !important;
    text-align: left !important; 
    transition: all 0.2s ease !important;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
}
.fu-row button:hover {
    background: #f1f5f9 !important;
    border-color: #cbd5e1 !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05) !important;
}

/* Suggestion row */
.sug-row {
    justify-content: center;
    gap: 12px !important;
    margin-top: 20px !important;
}
.sug-row button { 
    border-radius: 12px !important;
    padding: 10px 16px !important;
    background: white !important;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px -1px rgba(0, 0, 0, 0.1) !important;
    border: 1px solid #e2e8f0 !important;
    color: #475569 !important;
    font-size: 0.82rem !important; 
    font-weight: 500 !important;
    transition: all 0.2s ease !important;
}
.sug-row button:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1) !important;
    color: #2563eb !important;
    border-color: #bfdbfe !important;
}

/* Input Bar Styling */
#msg-box {
    border-radius: 24px !important;
    background: white !important;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05) !important;
    border: 1px solid #e2e8f0 !important;
    overflow: hidden !important;
}
#msg-box textarea { 
    font-size: 0.95rem !important; 
    padding: 14px 20px !important;
    border: none !important;
}
#msg-box textarea:focus {
    box-shadow: none !important;
}

/* Links in messages */
#chatbot a { 
    color: #2563eb !important; 
    font-weight: 600 !important; 
    text-decoration: none !important; 
    border-bottom: 1px solid transparent;
    transition: all 0.2s ease;
}
#chatbot a:hover {
    border-bottom: 1px solid #2563eb;
}

/* Hide footer */
footer { display: none !important; }

/* Fix dropdown arrow collision and layout */
#history-dropdown .wrap {
    position: relative !important;
}
#history-dropdown .wrap .head .icon {
    position: absolute !important;
    right: 12px !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    pointer-events: none !important;
}
#history-dropdown .wrap .head input {
    padding-right: 40px !important;
    text-overflow: ellipsis !important;
}
#history-dropdown .wrap .options {
    width: 100% !important;
}
"""

HEAD_JS = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    const observer = new MutationObserver((mutations) => {
        const inputs = document.querySelectorAll('#history-dropdown input');
        inputs.forEach(input => {
            if (input.getAttribute('autocomplete') !== 'off') {
                input.setAttribute('autocomplete', 'off');
                input.setAttribute('name', 'no-autocomplete-' + Math.random());
                input.setAttribute('data-lpignore', 'true'); // LastPass ignore
            }
        });
    });
    observer.observe(document.body, { childList: true, subtree: true });
});
</script>
"""

# ─── Suggestions ─────────────────────────────────────────────────────────────

SUGGESTIONS = []

# ─── Core chat logic ─────────────────────────────────────────────────────────


def respond(message, history, session_id, top_k, user_state):
    """
    Generator that yields (history, session_id, fu1, fu2, fu3, sug_visible, msg)
    """
    if not user_state or not user_state.get("authenticated"):
        history = list(history or [])
        history.append({"role": "assistant", "content": "⚠️ Please login to use the bot."})
        yield history, session_id, gr.skip(), gr.skip(), gr.skip(), gr.skip(), ""
        return

    if not message or not message.strip():
        yield history, session_id, gr.skip(), gr.skip(), gr.skip(), gr.skip(), ""
        return

    if not session_id:
        session_id = new_session_id()

    history = list(history or [])

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": "⏳ Thinking…"})
    yield (
        history,
        session_id,
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        "",
    )

    try:
        r = requests.post(
            f"{API_BASE}/agents/coaction-underwriting/invoke",
            json={"input_text": message, "session_id": session_id or "", "top_k": top_k},
            headers=get_headers(user_state.get("token", "")),
            timeout=120,
        )
        if not r.ok:
            try:
                err_msg = r.json().get("detail", r.text)
            except Exception:
                err_msg = r.text
            raise Exception(f"API Error {r.status_code}: {err_msg}")

        data = r.json()

        if "session_id" in data and not session_id:
            session_id = data["session_id"]

        answer = data.get("answer", "")
        if data.get("status") == "error":
            answer = f"⚠️ {answer}"

        citations = data.get("citations", [])
        if citations:
            answer += "\n\n**Sources:**\n"
            for c in citations:
                manual = c.get("manual_name") or "Binding Authority Manual"
                title = c.get("title") or c.get("source_id") or "Source"
                uri = c.get("uri") or "#"
                answer += f"\nSource Manual: {manual}\nSection: {title}\nLink: {uri}\n"

        history[-1]["content"] = answer
        fups = data.get("metadata", {}).get("follow_up_questions", [])
        fu_updates = []
        for i in range(3):
            if i < len(fups):
                fu_updates.append(gr.update(value=fups[i], visible=True))
            else:
                fu_updates.append(gr.update(visible=False))

        yield (history, session_id, *fu_updates, gr.update(visible=False), "")

    except Exception as exc:
        history[-1]["content"] = f"⚠️ {exc}"
        yield (
            history,
            session_id,
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            "",
        )


def on_followup(text, history, session_id, top_k, user_state):
    yield from respond(text, history, session_id, top_k, user_state)


def on_clear():
    return (
        [],  # chatbot
        "",  # session_state
        gr.update(visible=False),  # fu1
        gr.update(visible=False),  # fu2
        gr.update(visible=False),  # fu3
        gr.update(visible=True),  # suggestions
        "",  # msg
    )


# ─── Build App ───────────────────────────────────────────────────────────────


def build():
    with gr.Blocks(title="Coaction Binding Authority Assistant", head=HEAD_JS) as app:
        session_state = gr.State("")
        user_state = gr.State(
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""}
        )

        # ── Sidebar (History & Settings) ──
        with gr.Sidebar(label="Coaction Assistant", open=True):
            new_chat_btn = gr.Button("➕ New Chat", variant="primary")
            history_dropdown = gr.Dropdown(
                label="Recent Chats", choices=[], interactive=True, elem_id="history-dropdown"
            )

            with gr.Accordion("⚙ Settings", open=False):
                top_k = gr.Slider(1, 20, value=5, step=1, label="Search depth")
                gr.HTML(
                    f'<p style="font-size:0.72rem;color:#64748b;margin-top:8px;">'
                    f"API: {api_health()}</p>"
                )

            with gr.Accordion(
                "📚 Knowledge Base Management", open=False, visible=False
            ) as kb_accordion:
                gr.Markdown("Create a new Knowledge Base (Underwriter only)")
                kb_name = gr.Textbox(label="KB Name", placeholder="e.g. my-new-kb")
                kb_desc = gr.Textbox(label="Description", placeholder="Description of this KB")
                kb_bucket = gr.Textbox(label="S3 Bucket", value="vega-binding-authority")
                kb_prefix = gr.Textbox(label="S3 Prefix", placeholder="e.g. docs/")
                kb_create_btn = gr.Button("Create KB", variant="secondary")
                kb_status = gr.Markdown("")

        with gr.Column(visible=True) as auth_col:
            gr.Markdown("### Login Required")
            with gr.Tab("Signup"):
                su_name = gr.Textbox(label="Name")
                su_email = gr.Textbox(label="Email")
                su_password = gr.Textbox(label="Password", type="password")
                su_role = gr.Dropdown(list(ALLOWED_ROLES), value="agent", label="Role")
                su_btn = gr.Button("Create account", variant="primary")
                su_status = gr.Markdown("")

                # Verification Section (hidden initially)
                with gr.Column(visible=False) as verify_col:
                    gr.Markdown("---")
                    gr.Markdown("#### 📧 Verify your Email")
                    gr.Markdown("Please enter the code sent to your inbox.")
                    v_code = gr.Textbox(label="Verification Code", placeholder="123456")
                    v_btn = gr.Button("Verify & Confirm", variant="primary")
                    v_status = gr.Markdown("")

            with gr.Tab("Login"):
                li_email = gr.Textbox(label="Email")
                li_password = gr.Textbox(label="Password", type="password")
                li_btn = gr.Button("Login", variant="primary")
                li_status = gr.Markdown("")

        # ── Main column (locked height) ──
        with gr.Column(elem_id="chat-col", visible=False) as chat_col:
            user_badge = gr.Markdown("")

            chatbot = gr.Chatbot(
                elem_id="chatbot",
                height=680,
                show_label=False,
                avatar_images=(
                    None,
                    "https://www.coactionspecialty.com/favicon.ico",
                ),
                placeholder=(
                    '<div style="text-align:center;padding:12rem 1rem;color:#94a3b8;">'
                    '<p style="font-size:1.1rem;font-weight:600;color:#1e293b;">'
                    "Coaction Binding Authority Assistant</p>"
                    '<p style="font-size:0.82rem;">Ask about class codes, '
                    "coverage options, or manual guidelines.</p></div>"
                ),
            )

            # ── Follow-up buttons ──
            with gr.Row(elem_classes=["fu-row"]):
                fu1 = gr.Button(visible=False, size="sm")
                fu2 = gr.Button(visible=False, size="sm")
                fu3 = gr.Button(visible=False, size="sm")

            # ── Suggestion chips (hidden after first message) ──
            with gr.Row(visible=True, elem_classes=["sug-row"]) as sug_row:
                sug_btns = []
                for txt in SUGGESTIONS:
                    sug_btns.append(gr.Button(txt, size="sm", variant="secondary"))

            # ── Input bar ──
            with gr.Row():
                msg = gr.Textbox(
                    elem_id="msg-box",
                    placeholder="Type your underwriting query…",
                    show_label=False,
                    scale=8,
                    lines=1,
                    max_lines=3,
                )
                send = gr.Button("Send", variant="primary", scale=1, min_width=80)
                clear = gr.Button("Clear", scale=1, min_width=70)
                logout = gr.Button("Logout", scale=1, min_width=70)

        # ── Wiring ──
        outs = [chatbot, session_state, fu1, fu2, fu3, sug_row, msg]
        ins = [msg, chatbot, session_state, top_k, user_state]

        # Send / Enter
        send.click(respond, ins, outs).then(refresh_dropdown, [user_state], [history_dropdown])
        msg.submit(respond, ins, outs).then(refresh_dropdown, [user_state], [history_dropdown])

        # Follow-ups
        for btn in (fu1, fu2, fu3):
            btn.click(on_followup, [btn, chatbot, session_state, top_k, user_state], outs)

        # Suggestion chips
        for sb in sug_btns:
            sb.click(lambda t=sb.value: t, None, [msg]).then(respond, ins, outs)

        su_btn.click(signup_user, [su_name, su_email, su_password, su_role], [su_status]).then(
            lambda r: gr.update(visible=True) if "successful" in r else gr.update(visible=False),
            [su_status],
            [verify_col],
        )

        v_btn.click(verify_user, [su_email, v_code], [v_status])

        kb_create_btn.click(
            create_kb, [kb_name, kb_desc, kb_bucket, kb_prefix, user_state], [kb_status]
        )

        li_btn.click(
            login_user,
            [li_email, li_password],
            [user_state, li_status, chat_col, auth_col, user_badge, history_dropdown, kb_accordion],
        )

        logout.click(
            logout_user,
            None,
            [
                user_state,
                li_status,
                chat_col,
                auth_col,
                user_badge,
                chatbot,
                session_state,
                fu1,
                fu2,
                fu3,
                sug_row,
                msg,
                history_dropdown,
                kb_accordion,
            ],
        )

        def clear_chat(user_state):
            choices = []
            if user_state and user_state.get("token"):
                try:
                    resp = requests.get(
                        f"{API_BASE}/sessions", headers=get_headers(user_state.get("token"))
                    )
                    if resp.ok:
                        sessions = resp.json()
                        for s in sessions:
                            dt_str = s.get("last_accessed", "")
                            title = s.get("title", "New Chat")
                            try:
                                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                                date_fmt = dt.strftime("%Y-%m-%d %H:%M")
                                display_text = f"[{date_fmt}] {title}"
                            except Exception:
                                display_text = title
                            choices.append((display_text, s["session_id"]))
                except Exception:
                    pass
            return (
                [],
                "",
                gr.update(value="", visible=False),
                gr.update(value="", visible=False),
                gr.update(value="", visible=False),
                gr.update(visible=True),
                "",
                gr.update(value=None, choices=choices),
            )

        clear.click(
            clear_chat,
            [user_state],
            [chatbot, session_state, fu1, fu2, fu3, sug_row, msg, history_dropdown],
        )

        new_chat_btn.click(
            clear_chat,
            [user_state],
            [chatbot, session_state, fu1, fu2, fu3, sug_row, msg, history_dropdown],
        )

        # Load past session when dropdown changes
        history_dropdown.change(
            load_session,
            [history_dropdown, user_state],
            [chatbot, session_state, fu1, fu2, fu3, sug_row],
        )

    return app


# ─── Launch ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ui = build()

    if fastapi_app:
        print("🚀 Starting Unified Deployment (UI + API)...")
        # Mount the UI onto the FastAPI app
        # This makes the UI available at / and the API at /v1
        gr.mount_gradio_app(fastapi_app, ui, path="/")

        import uvicorn

        uvicorn.run(fastapi_app, host="0.0.0.0", port=8080)
    else:
        print("🏃 Starting Standalone UI...")
        ui.launch(
            server_name="0.0.0.0",
            server_port=7860,
            theme=THEME,
            css=CSS,
        )
