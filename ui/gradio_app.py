"""
Coaction Binding Authority Assistant — Gradio 6.5 UI
Minimalist monochrome design with real-time streaming.
"""

import gradio as gr
import requests
import os
import uuid
from datetime import datetime
from urllib.parse import urlparse

# Optional: import FastAPI app for unified `python ui/gradio_app.py` launch
try:
    from app.main import app as fastapi_app
except ImportError:
    fastapi_app = None

ALLOWED_ROLES = ("agent", "underwriter", "external")


def resolve_api_base() -> str:
    """Absolute API base URL for server-side requests (requests requires http://)."""
    explicit = (os.getenv("API_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        if not explicit.startswith(("http://", "https://")):
            explicit = f"http://{explicit.lstrip('/')}"
        return explicit
    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT") or os.getenv("PORT", "8000")
    return f"http://{host}:{port}/v1"


API_BASE = resolve_api_base()

# ─── Helpers ─────────────────────────────────────────────────────────────────


def api_error_detail(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", ()))
                parts.append(f"{loc}: {item.get('msg', item)}" if loc else str(item.get("msg", item)))
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else str(detail)
    return str(detail)


def connection_error_message(exc: Exception) -> str:
    msg = str(exc)
    if any(
        token in msg
        for token in (
            "Connection refused",
            "No connection could be made",
            "Name or service not known",
            "timed out",
        )
    ):
        return (
            f"Cannot reach API at {API_BASE}. "
            "Start the backend: python -m uvicorn app.main:app --reload --port 8000"
        )
    return msg


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
            detail = api_error_detail(r)
            if r.status_code == 503 and "not initialized" in detail.lower():
                detail += " Set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID in .env."
            return f"Signup failed: {detail}"
        return "Signup successful. Check your email for a verification code, then verify below."
    except Exception as exc:
        return f"Signup failed: {connection_error_message(exc)}"


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
            return f"Verification failed: {api_error_detail(r)}"
        return "Verification successful. You can now log in."
    except Exception as exc:
        return f"Verification failed: {connection_error_message(exc)}"


def login_user(email: str, password: str):
    if not email or not password:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            "Please enter both email and password.",
            gr.Column(visible=False),
            gr.Column(visible=True),
            "",
            gr.Dropdown(choices=[]),
            gr.Accordion(visible=False),
        )
    try:
        r = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": (email or "").strip(), "password": password or ""},
            timeout=10,
        )
        if r.status_code >= 400:
            detail = api_error_detail(r)
            if r.status_code == 503 and "not initialized" in detail.lower():
                detail += " Set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID in .env."
            return (
                {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
                f"Login failed: {detail}",
                gr.Column(visible=False),
                gr.Column(visible=True),
                "",
                gr.Dropdown(choices=[]),
                gr.Accordion(visible=False),
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
            gr.Column(visible=True),
            gr.Column(visible=False),
            welcome,
            gr.Dropdown(choices=dropdown_choices),
            gr.Accordion(visible=is_underwriter),
        )
    except Exception as exc:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            f"Login failed: {connection_error_message(exc)}",
            gr.Column(visible=False),
            gr.Column(visible=True),
            "",
            gr.Dropdown(choices=[]),
            gr.Accordion(visible=False),
        )


def logout_user():
    return (
        {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
        "Logged out.",
        gr.Column(visible=False),
        gr.Column(visible=True),
        "",
        [],  # chatbot
        "",  # session_state
        gr.Button(value="", visible=False),  # fu1
        gr.Button(value="", visible=False),  # fu2
        gr.Button(value="", visible=False),  # fu3
        gr.Row(visible=True),  # suggestions
        "",  # msg
        gr.Dropdown(choices=[]),  # history_dropdown
        gr.Accordion(visible=False),  # kb_accordion
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
    return gr.Dropdown(choices=choices)


def load_session(session_id, user_state):
    hide_btn = gr.Button(visible=False)
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
        parsed = urlparse(API_BASE)
        health_url = f"{parsed.scheme}://{parsed.netloc}/health"
        r = requests.get(health_url, timeout=2)
        return "Online" if r.ok else "Degraded"
    except Exception:
        return "Offline"


# ─── Theme ───────────────────────────────────────────────────────────────────

THEME = gr.themes.Monochrome(
    font=[gr.themes.GoogleFont("IBM Plex Sans"), "ui-sans-serif", "system-ui", "sans-serif"],
    radius_size=gr.themes.sizes.radius_sm,
).set(
    body_background_fill="#fafafa",
    block_background_fill="#ffffff",
    block_border_width="1px",
    block_border_color="#e5e5e5",
    block_label_background_fill="#f5f5f5",
    block_label_text_color="#525252",
    button_primary_background_fill="#171717",
    button_primary_background_fill_hover="#404040",
    button_primary_text_color="#ffffff",
    button_secondary_background_fill="#ffffff",
    button_secondary_border_color="#d4d4d4",
    button_secondary_text_color="#404040",
    border_color_primary="#e5e5e5",
    color_accent_soft="#f5f5f5",
    panel_background_fill="#ffffff",
    input_background_fill="#ffffff",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
/* Minimalist monochrome */
body, .gradio-container {
    background: #fafafa !important;
    color: #171717 !important;
}

/* Hide Gradio default footer (Use via API · Built with Gradio · Settings) */
footer, .gradio-container > footer,
div.footer, .built-with, .show-api,
[class*="footer"], [class*="Footer"],
.gradio-container .wrap > .show-api { display: none !important; visibility: hidden !important; height: 0 !important; overflow: hidden !important; }

.sidebar {
    background: #ffffff !important;
    border-right: 1px solid #e5e5e5 !important;
}

#chatbot {
    height: 680px !important;
    border: 1px solid #e5e5e5 !important;
    background: #ffffff !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    padding: 12px !important;
}

.message-row.user .message, #chatbot .message.user {
    background: #171717 !important;
    color: #ffffff !important;
    border-radius: 12px 12px 2px 12px !important;
    padding: 12px 16px !important;
    border: none !important;
    box-shadow: none !important;
}
.message-row.user .message *, #chatbot .message.user * { color: #ffffff !important; }

.message-row.bot .message, #chatbot .message.bot {
    background: #f5f5f5 !important;
    color: #262626 !important;
    border-radius: 12px 12px 12px 2px !important;
    padding: 12px 16px !important;
    border: 1px solid #e5e5e5 !important;
    box-shadow: none !important;
}

.message-wrap {
    font-size: 0.92rem !important;
    line-height: 1.6 !important;
}

.fu-row {
    margin-top: 8px !important;
    gap: 6px !important;
}
.fu-row button {
    background: #ffffff !important;
    border: 1px solid #d4d4d4 !important;
    color: #404040 !important;
    border-radius: 999px !important;
    font-size: 0.8rem !important;
    padding: 6px 14px !important;
    box-shadow: none !important;
}
.fu-row button:hover {
    background: #f5f5f5 !important;
    border-color: #a3a3a3 !important;
}

.sug-row {
    justify-content: center;
    gap: 8px !important;
    margin-top: 12px !important;
}
.sug-row button {
    border-radius: 6px !important;
    padding: 8px 14px !important;
    background: #ffffff !important;
    border: 1px solid #d4d4d4 !important;
    color: #525252 !important;
    font-size: 0.8rem !important;
    box-shadow: none !important;
}
.sug-row button:hover {
    background: #f5f5f5 !important;
    border-color: #737373 !important;
    color: #171717 !important;
}

#msg-box {
    border-radius: 8px !important;
    background: #ffffff !important;
    border: 1px solid #d4d4d4 !important;
    box-shadow: none !important;
}
#msg-box textarea {
    font-size: 0.92rem !important;
    padding: 12px 14px !important;
}

#chatbot a {
    color: #171717 !important;
    font-weight: 600 !important;
    text-decoration: underline !important;
}

/* Hide ALL Gradio 6.x footer, API, and Settings elements - Ultra Aggressive */
footer, 
.gradio-container .prose.footer, 
.gradio-container .built-with, 
.show-api, 
.settings-button, 
.settings-menu,
#footer,
[class*="footer"],
[class*="built-with"],
[class*="show-api"] { 
    display: none !important; 
    visibility: hidden !important;
    height: 0 !important;
    padding: 0 !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

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


def add_user_message(message, history, session_id, user_state):
    """Step 1: Immediately show user message.
    
    This runs instantly before the API call, so the user sees their
    message on the right side without delay. Gradio's built-in loading
    spinner handles the waiting indicator.
    
    IMPORTANT: This function must NOT output to fu1/fu2/fu3/sug_row.
    In Gradio 6.14, if step1 and step2 both write to the same components
    in a .then() chain, the second write gets silently dropped.
    """
    if not user_state or not user_state.get("authenticated"):
        history = list(history or [])
        history.append({"role": "assistant", "content": "⚠️ Please login to use the bot."})
        return history, session_id, ""

    if not message or not message.strip():
        return history or [], session_id, ""

    if not session_id:
        session_id = new_session_id()

    history = list(history or [])
    history.append({"role": "user", "content": message})

    return history, session_id, ""  # clear msg box


def get_response(history, session_id, top_k, user_state):
    """Step 2: Make API call and update the assistant response.
    
    Called after add_user_message, so the user message is already visible.
    """
    if not history or len(history) < 1:
        return history or [], session_id, gr.skip(), gr.skip(), gr.skip(), gr.skip()

    # The last message is the user message (no more thinking placeholder)
    raw_content = history[-1].get("content", "")

    # Gradio 6.x may store content as a list of blocks: [{"text": "hello", "type": "text"}]
    if isinstance(raw_content, list):
        last_user_msg = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw_content
        ).strip()
    else:
        last_user_msg = str(raw_content).strip()

    if not last_user_msg or last_user_msg == "⚠️ Please login to use the bot.":
        return history, session_id, gr.skip(), gr.skip(), gr.skip(), gr.skip()

    try:
        r = requests.post(
            f"{API_BASE}/agents/coaction-underwriting/invoke",
            json={"input_text": last_user_msg, "session_id": session_id or "", "top_k": top_k},
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

        history.append({"role": "assistant", "content": answer})
        fups = data.get("metadata", {}).get("follow_up_questions", [])
        fu_updates = []
        for i in range(3):
            if i < len(fups):
                fu_updates.append(gr.Button(value=fups[i], visible=True, size="sm"))
            else:
                fu_updates.append(gr.Button(visible=False))

        return (history, session_id, *fu_updates, gr.Row(visible=False))

    except Exception as exc:
        history.append({"role": "assistant", "content": f"⚠️ {exc}"})
        return (
            history,
            session_id,
            gr.Button(visible=False),
            gr.Button(visible=False),
            gr.Button(visible=False),
            gr.Row(visible=False),
        )


def on_followup(text, history, session_id, top_k, user_state):
    """Handle follow-up button clicks — same two-step flow."""
    step1 = add_user_message(text, history, session_id, user_state)
    # step1 returns: (history, session_id, msg, fu1, fu2, fu3, sug_row)
    updated_history = step1[0]
    updated_session = step1[1]
    return get_response(updated_history, updated_session, top_k, user_state)


def on_clear():
    return (
        [],  # chatbot
        "",  # session_state
        gr.Button(value="", visible=False),  # fu1
        gr.Button(value="", visible=False),  # fu2
        gr.Button(value="", visible=False),  # fu3
        gr.Row(visible=True),  # suggestions
        "",  # msg
    )


# ─── Build App ───────────────────────────────────────────────────────────────


def build():
    with gr.Blocks(title="Coaction Binding Authority Assistant", theme=THEME) as app:
        app.head = HEAD_JS
        app.css = CSS
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
                    f'<p style="font-size:0.72rem;color:#737373;margin-top:8px;">'
                    f"API ({API_BASE}): {api_health()}</p>"
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
                    '<div style="text-align:center;padding:12rem 1rem;color:#a3a3a3;">'
                    '<p style="font-size:1.05rem;font-weight:600;color:#171717;">'
                    "Coaction Binding Authority Assistant</p>"
                    '<p style="font-size:0.82rem;color:#525252;">Ask about class codes, '
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
        # Step 1: only updates chatbot, session_state, msg (NO fu buttons)
        step1_outs = [chatbot, session_state, msg]
        step1_ins = [msg, chatbot, session_state, user_state]

        # Step 2: updates chatbot, session_state AND exclusively owns fu1, fu2, fu3, sug_row
        step2_outs = [chatbot, session_state, fu1, fu2, fu3, sug_row]
        step2_ins = [chatbot, session_state, top_k, user_state]

        # Send / Enter — two-step chain for immediate user message display
        send.click(add_user_message, step1_ins, step1_outs).then(
            get_response, step2_ins, step2_outs
        ).then(refresh_dropdown, [user_state], [history_dropdown])

        msg.submit(add_user_message, step1_ins, step1_outs).then(
            get_response, step2_ins, step2_outs
        ).then(refresh_dropdown, [user_state], [history_dropdown])

        # Follow-ups — same two-step chain for immediate message display
        for btn in (fu1, fu2, fu3):
            btn.click(lambda t: t, [btn], [msg]).then(
                add_user_message, step1_ins, step1_outs
            ).then(
                get_response, step2_ins, step2_outs
            )

        # Suggestion chips
        for sb in sug_btns:
            sb.click(lambda t=sb.value: t, None, [msg]).then(
                add_user_message, step1_ins, step1_outs
            ).then(
                get_response, step2_ins, step2_outs
            )

        su_btn.click(signup_user, [su_name, su_email, su_password, su_role], [su_status]).then(
            lambda r: gr.Column(visible=True) if "successful" in r else gr.Column(visible=False),
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
                gr.Button(value="", visible=False),
                gr.Button(value="", visible=False),
                gr.Button(value="", visible=False),
                gr.Row(visible=True),
                "",
                gr.Dropdown(value=None, choices=choices),
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
    api_port = int(os.getenv("API_PORT") or os.getenv("PORT", "8000"))

    print(f"API base for UI callbacks: {API_BASE}")

    if fastapi_app:
        print("Starting unified deployment (UI + API)...")
        gr.mount_gradio_app(fastapi_app, ui, path="/")

        import uvicorn

        uvicorn.run(fastapi_app, host="0.0.0.0", port=api_port)
    else:
        print("Starting standalone UI (expects API on same host/port as API_BASE)...")
        ui.launch(
            server_name="0.0.0.0",
            server_port=7860,
            theme=THEME,
            css=CSS,
        )
