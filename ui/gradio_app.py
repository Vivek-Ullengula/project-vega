"""
Coaction Binding Authority Assistant — Gradio 6.5 UI
Minimalist monochrome design with real-time streaming.
"""
import gradio as gr
import requests
import json
import os
import uuid

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")
ALLOWED_ROLES = ("agent", "underwriter", "external")

# ─── Helpers ─────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    return str(uuid.uuid4())


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



def login_user(email: str, password: str):
    if not email or not password:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            "Please enter both email and password.",
            gr.update(visible=False),
            gr.update(visible=True),
            ""
        )
    try:
        r = requests.post(
            f"{API_BASE}/auth/login",
            json={"email": (email or "").strip(), "password": password or ""},
            timeout=10,
        )
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text)
            return (
                {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
                f"Login failed: {detail}",
                gr.update(visible=False),
                gr.update(visible=True),
                ""
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
        role_key = str(session_user.get('role', '')).strip().lower()
        user_name = session_user['name']
        if role_key == 'underwriter':
            welcome = f"Welcome to the Underwriter Portal, {user_name}."
        elif role_key == 'agent':
            welcome = f"Welcome to the Agent Portal, {user_name}."
        else:
            welcome = f"Welcome, {user_name}."
        return (
            session_user, 
            welcome, 
            gr.update(visible=True), 
            gr.update(visible=False), 
            welcome
        )
    except Exception as exc:
        return (
            {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
            f"Login failed: {exc}",
            gr.update(visible=False),
            gr.update(visible=True),
            ""
        )


def logout_user():
    return (
        {"authenticated": False, "name": "", "email": "", "role": "", "token": ""},
        "Logged out.",
        gr.update(visible=False),
        gr.update(visible=True),
        "",
        [],                          # chatbot
        "",                          # session_state
        gr.update(value="", visible=False),    # fu1
        gr.update(value="", visible=False),    # fu2
        gr.update(value="", visible=False),    # fu3
        gr.update(visible=True),     # suggestions
        ""                           # msg
    )



def api_health() -> str:
    try:
        r = requests.get(API_BASE.replace("/api/v1", "/health"), timeout=2)
        return "🟢 Online" if r.ok else "🟡 Degraded"
    except Exception:
        return "🔴 Offline"

# ─── Theme ───────────────────────────────────────────────────────────────────

THEME = gr.themes.Monochrome(
    font=gr.themes.GoogleFont("Inter"),
    radius_size=gr.themes.sizes.radius_sm,
)

# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
/* Lock the chat column so nothing shrinks */
#chat-col { min-height: 820px; }

/* Chatbot fixed height */
#chatbot { height: 680px !important; }

/* Smaller text in messages */
#chatbot .message-wrap { font-size: 0.88rem !important; line-height: 1.55 !important; }

/* Follow-up row */
.fu-row button { font-size: 0.8rem !important; text-align: left !important; }

/* Suggestion row */
.sug-row button { font-size: 0.78rem !important; }

/* Input */
#msg-box textarea { font-size: 0.88rem !important; }

/* Links in messages */
#chatbot a { color: #334155 !important; font-weight: 600 !important; text-decoration: underline !important; }

/* Hide footer */
footer { display: none !important; }
"""

# ─── Suggestions ─────────────────────────────────────────────────────────────

SUGGESTIONS = [
    "What is class code 10040?",
    "Binding authority property manual overview",
    "What are the GL submission requirements?",
    "What operations are prohibited?",
]

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
    yield (history, session_id,
           gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
           gr.update(visible=False), "")

    try:
        with requests.post(
            f"{API_BASE}/query",
            json={"query": message, "session_id": session_id or "", "top_k": top_k},
            headers={"Authorization": f"Bearer {user_state.get('token', '')}"},
            stream=True, timeout=120,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8")
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])

                if data.get("type") == "status":
                    history[-1]["content"] = data["message"]
                    yield (history, session_id,
                           gr.update(visible=False), gr.update(visible=False),
                           gr.update(visible=False), gr.update(visible=False), "")

                elif data.get("type") == "final":
                    if "session_id" in data and not session_id:
                        session_id = data["session_id"]
                        
                    answer = data.get("answer", "")
                    history[-1]["content"] = answer
                    fups = data.get("follow_up_questions", [])
                    fu_updates = []
                    for i in range(3):
                        if i < len(fups):
                            fu_updates.append(gr.update(value=fups[i], visible=True))
                        else:
                            fu_updates.append(gr.update(visible=False))
                    
                    yield (history, session_id, *fu_updates,
                           gr.update(visible=False), "")

                elif data.get("type") == "error":
                    history[-1]["content"] = f"⚠️ {data['message']}"
                    yield (history, session_id,
                           gr.update(visible=False), gr.update(visible=False),
                           gr.update(visible=False), gr.update(visible=False), "")

    except Exception as exc:
        history[-1]["content"] = f"⚠️ {exc}"
        yield (history, session_id,
               gr.update(visible=False), gr.update(visible=False),
               gr.update(visible=False), gr.update(visible=False), "")


def on_followup(text, history, session_id, top_k, user_state):
    yield from respond(text, history, session_id, top_k, user_state)


def on_clear():
    return (
        [],                          # chatbot
        "",                          # session_state
        gr.update(visible=False),    # fu1
        gr.update(visible=False),    # fu2
        gr.update(visible=False),    # fu3
        gr.update(visible=True),     # suggestions
        ""                           # msg
    )

# ─── Build App ───────────────────────────────────────────────────────────────

def build():
    with gr.Blocks(title="Coaction Binding Authority Assistant") as app:

        session_state = gr.State("")
        user_state = gr.State({"authenticated": False, "name": "", "email": "", "role": "", "token": ""})

        # ── Settings sidebar ──
        with gr.Sidebar(label="⚙ Settings", open=False):
            top_k = gr.Slider(1, 20, value=5, step=1, label="Search depth")
            gr.HTML(f'<p style="font-size:0.72rem;color:#64748b;margin-top:8px;">'
                    f'API: {api_health()}</p>')

        with gr.Column(visible=True) as auth_col:
            gr.Markdown("### Login Required")
            with gr.Tab("Signup"):
                su_name = gr.Textbox(label="Name")
                su_email = gr.Textbox(label="Email")
                su_password = gr.Textbox(label="Password", type="password")
                su_role = gr.Dropdown(list(ALLOWED_ROLES), value="agent", label="Role")
                su_btn = gr.Button("Create account", variant="primary")
                su_status = gr.Markdown("")

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
                    'Coaction Binding Authority Assistant</p>'
                    '<p style="font-size:0.82rem;">Ask about class codes, '
                    'coverage options, or manual guidelines.</p></div>'
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
        outs  = [chatbot, session_state, fu1, fu2, fu3, sug_row, msg]
        ins   = [msg, chatbot, session_state, top_k, user_state]

        # Send / Enter
        send.click(respond, ins, outs)
        msg.submit(respond, ins, outs)

        # Follow-ups
        for btn in (fu1, fu2, fu3):
            btn.click(on_followup, [btn, chatbot, session_state, top_k, user_state], outs)

        # Suggestion chips
        for sb in sug_btns:
            sb.click(
                lambda t=sb.value: t, None, [msg]
            ).then(
                respond, ins, outs
            )

        su_btn.click(signup_user, [su_name, su_email, su_password, su_role], [su_status])
        
        li_btn.click(
            login_user,
            [li_email, li_password],
            [user_state, li_status, chat_col, auth_col, user_badge],
        )
        logout.click(
            logout_user,
            None,
            [user_state, li_status, chat_col, auth_col, user_badge, chatbot, session_state, fu1, fu2, fu3, sug_row, msg],
        )

        def clear_chat():
            return [], "", gr.update(value="", visible=False), gr.update(value="", visible=False), gr.update(value="", visible=False), gr.update(visible=True), ""
            
        clear.click(
            clear_chat,
            None,
            [chatbot, session_state, fu1, fu2, fu3, sug_row, msg]
        )

    return app


# ─── Launch ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build().launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=THEME,
        css=CSS,
    )
