import json
import uuid
import base64
import requests
import streamlit as st
try:
    import streamlit.components.v1 as components
except Exception:
    components = None
from pathlib import Path

# Avatar morado claro: usuario y bot
USER_AVATAR = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>"
    "<rect width='64' height='64' rx='16' fill='%23c084fc'/>"
    "<path fill='white' d='M32 18c-4.4 0-8 3.6-8 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8zm0 18c-6.6 0-12 3.6-12 8v2c0 1.1.9 2 2 2h20c1.1 0 2-.9 2-2v-2c0-4.4-5.4-8-12-8z'/>"
    "</svg>"
)

BOT_AVATAR = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>"
    "<rect width='64' height='64' rx='16' fill='%23c084cc'/>"
    "<path fill='white' d='M28 14h8v4h4c2.2 0 4 1.8 4 4v10c0 2.2-1.8 4-4 4h-4v4l-6-4h-6c-2.2 0-4-1.8-4-4V22c0-2.2 1.8-4 4-4h4v-4zm-4 12a2 2 0 104 0 2 2 0 00-4 0zm12 0a2 2 0 104 0 2 2 0 00-4 0z'/>"
    "</svg>"
)

# ---------------- Config fija (sin sidebar) ----------------
BASE_URL = "http://127.0.0.1:3000"
APP_NAME = "help_agent"
USER_ID = "u_web"

def _headers(sse: bool = False) -> dict:
    h = {"Content-Type": "application/json"}
    if sse:
        h["Accept"] = "text/event-stream"
    return h

def ensure_session(url: str, app: str, user: str, sid: str, state: dict | None):
    get_url = f"{url}/apps/{app}/users/{user}/sessions/{sid}"
    r = requests.get(get_url, headers=_headers(), timeout=10)
    if r.status_code == 200:
        return r.json()
    payload = {"state": state or {}}
    r = requests.post(get_url, json=payload, headers=_headers(), timeout=15)
    if r.status_code == 200:
        return r.json()
    if r.status_code == 409:
        r2 = requests.get(get_url, headers=_headers(), timeout=10)
        r2.raise_for_status()
        return r2.json()
    r.raise_for_status()

# ---------------- UI ----------------
st.set_page_config(
    page_title="Agentic - Ayuda y Apoyo",
    layout="wide",
    page_icon="🆘",
    menu_items={"Get Help": None, "Report a Bug": None, "About": "Agentic - Ayuda y Apoyo"},
)

# Footer de estado (icono) y ajuste para que el input no se superponga
st.markdown(
    """
    <style>
    /* Footer de estado */
    .status-footer {
        position: fixed; left: 0; right: 0; bottom: 0; height: 40px;
        display: flex; align-items: center; justify-content: center; gap: 8px;
        background: rgba(255,255,255,0.98); border-top: 1px solid rgba(0,0,0,0.06);
        z-index: 9999;
    }
    .status-footer .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

    /* Input flotante fijo y centrado */
    div[data-testid="stChatInput"] {
        position: fixed; left: 50%; transform: translateX(-50%);
        bottom: 56px; /* por encima del footer */
        width: min(900px, 92vw);
        z-index: 10000;
        background: transparent;
        padding: 0; margin: 0;
    }
    /* Estética tipo "flotante" */
    div[data-testid="stChatInput"] > div {
        border-radius: 9999px;
        background: rgba(255,255,255,0.96);
        backdrop-filter: blur(6px);
        box-shadow: 0 10px 30px rgba(0,0,0,0.12);
        padding: 8px 10px;
    }
    
    .st-b1{
        background: transparent !important;
    }

    /* Compacta el espacio entre burbujas de chat */
    .stChatMessage {
        margin-bottom: 1px !important;
    }

    /* Deja espacio al final del contenido para no tapar mensajes */
    .stApp > .main > div.block-container {
        padding-bottom: 180px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Logo en header si existe
logo_path = Path(__file__).parent / "assets" / "logo2.png"
if logo_path.exists():
    try:
        st.logo(str(logo_path), size="large")
    except Exception:
        pass

# Hero con imagen de fondo y textos superpuestos
def _encode_image(path: Path):
        try:
                mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                return mime, b64
        except Exception:
                return None

hero_img = None
for candidate in [
        Path(__file__).parent / "assets" / "hero2.jpg",
        Path(__file__).parent / "assets" / "hero2.png",
        Path(__file__).parent / "sunrise.jpg",
]:
        if candidate.exists():
                hero_img = _encode_image(candidate)
                if hero_img:
                        break

st.markdown(
        f"""
        <style>
            .hero {{
                position: relative;
                width: 100%;
                min-height: 40px;
                border-radius: 16px;
                overflow: hidden;
                margin: -100px 0 20px 0;
                box-shadow: 0 10px 30px rgba(0,0,0,0.25);
                backdrop-filter: blur(6px);
            }}
            .hero::before {{
                content: "";
                position: absolute; inset: 0;
                background: {'linear-gradient(180deg, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0.35) 100%), ' if hero_img else ''}url('{"data:" + hero_img[0] + ";base64," + hero_img[1] if hero_img else ""}');
                background-size: cover; background-position: center;
                filter: { 'none' if hero_img else 'brightness(0.95)' };
            }}
            .hero-overlay {{
                position: relative;
                z-index: 1;
                display: flex; flex-direction: column;
                align-items: center; justify-content: center;
                text-align: center;
                color: white;
                height: 100%;
                padding: 36px 16px;
            }}
            .hero h1 {{
                margin: 0 0 6px 0;
                font-size: clamp(1.4rem, 2.2vw + 1rem, 2.2rem);
                line-height: 1;
                font-weight: 700;
                text-shadow: 0 2px 8px rgba(0,0,0,0.35);
            }}
            .hero .caption {{
                margin: 0;
                font-size: clamp(0.7rem, 1vw + 0.6rem, 1.05rem);
                opacity: 0.95;
                text-shadow: 0 1px 4px rgba(0,0,0,0.35);
            }}
            {'' if hero_img else '.hero::before { background: linear-gradient(120deg, #4f46e5 0%, #7c3aed 100%); }'}
        </style>
        """,
        unsafe_allow_html=True,
)

st.markdown(
        """
        <div class="hero">
            <div class="hero-overlay">
                <h1>Agentic - CATI</h1>
                <p class="caption">Centro de Atención Temprana Inteligente.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
)

# Contenido principal
content = st.container()

def backend_online() -> bool:
    try:
        requests.get(BASE_URL, timeout=1)
        return True
    except requests.RequestException:
        return False

# Session ID nuevo por sesión (reload de navegador = nueva sesión)
if "session_id" not in st.session_state:
    st.session_state.session_id = f"s_{uuid.uuid4().hex[:8]}"
session_id = st.session_state.session_id

ONLINE = backend_online()
if ONLINE:
    try:
        ensure_session(BASE_URL, APP_NAME, USER_ID, session_id, state=None)
    except Exception:
        # silencioso para mantener la UI limpia
        pass

# --------- Historial y helpers ---------
if "history" not in st.session_state:
    st.session_state.history = []

def _emit_history():
    for role, msg in st.session_state.history:
        avatar = USER_AVATAR if role == "user" else BOT_AVATAR
        with st.chat_message(role, avatar=avatar):
            st.markdown(msg)

def _parse_events(events: list) -> str:
    out = []
    if isinstance(events, list):
        for ev in events:
            content = ev.get("content") or {}
            for part in content.get("parts") or []:
                t = part.get("text")
                if t:
                    out.append(t)
    return "".join(out).strip()

def run_once(text: str) -> str:
    url = f"{BASE_URL}/run"
    payload = {
        "app_name": APP_NAME,
        "user_id": USER_ID,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": text}]},
    }
    r = requests.post(url, json=payload, headers=_headers(), timeout=120)
    r.raise_for_status()
    return _parse_events(r.json())

# --------------- Chat (dentro del contenido) ---------------
with content:
    _emit_history()
    st.markdown('<div id="history-end"></div>', unsafe_allow_html=True)

    prompt = st.chat_input("Escribe tu mensaje")

    if prompt:
        st.session_state.history.append(("user", prompt))
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar=BOT_AVATAR):
            try:
                out = run_once(prompt) if ONLINE else "Revisa tu conexión e inténtalo de nuevo; si el problema persiste contacta con soporte."
            except requests.HTTPError as e:
                out = f"HTTP {e.response.status_code}: {e.response.text}"
            except Exception as e:
                out = str(e)
            st.markdown(out or "(sin texto)")
            st.session_state.history.append(("assistant", out or ""))

    # Auto-scroll suave como en app.py
    if components is not None:
        components.html(
            """
            <script>
              const el = document.getElementById('history-end');
              if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'end' }); }
            </script>
            """,
            height=0,
        )

status_color = "#16a34a" if ONLINE else "#9ca3af"
status_label = "Online" if ONLINE else "Offline"
st.markdown(
    f'<div class="status-footer"><span class="status-dot" style="background:{status_color}"></span>'
    f'<span style="color:#6b7280;font-size:0.9rem">{status_label}</span><span style="color:#6b7280;font-size:0.9rem">|</span><span style="color:#6b7280;font-size:0.9rem">Made by Jumperia </span></div>',
    unsafe_allow_html=True,
)
