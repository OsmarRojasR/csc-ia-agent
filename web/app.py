import os
import json
import uuid
import requests
import streamlit as st
try:
    import streamlit.components.v1 as components
except Exception:  # fallback si no está disponible
    components = None
from pathlib import Path

# ---------------- Config fija (sin sidebar) ----------------
BASE_URL = "http://127.0.0.1:3000"
APP_NAME = "insurance_agent"
USER_ID = "u_web"

# Utilidad: logo en el header nativo (usamos st.logo con el archivo en assets)

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
st.set_page_config(page_title="Agentic - Vendedor de Seguros", page_icon="🛡️", menu_items={"Get Help": None, "Report a Bug": None, "About": "Agentic - Vendedor de Seguros",})

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

    /* Deja espacio al final del contenido para no tapar mensajes */
    .stApp > .main > div.block-container {
        padding-bottom: 180px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# CSS: estructura en 3 filas y footer fijo al fondo
logo_path = Path(__file__).parent / "assets" / "logo.png"
if logo_path.exists():
    try:
        st.logo(str(logo_path), size="large")
    except Exception:
        pass

# Encabezado simple dentro del body
st.subheader("Agentic - Vendedor de Seguros")
st.caption("Powered by AWS")
st.markdown("---")

# Contenido principal
content = st.container()


def backend_online() -> bool:
    try:
        # Consideramos online si el host responde (cualquier status)
        requests.get(BASE_URL, timeout=1)
        return True
    except requests.RequestException:
        return False

# Session ID nuevo por sesión (reload de navegador = nueva sesión)
if "session_id" not in st.session_state:
    st.session_state.session_id = f"s_{uuid.uuid4().hex[:8]}"
session_id = st.session_state.session_id

# Crear/actualizar la sesión automáticamente
ONLINE = backend_online()
if ONLINE:
    try:
        ensure_session(BASE_URL, APP_NAME, USER_ID, session_id, state=None)
    except Exception:
        # Silencioso: evitamos mostrar mensajes en pantalla
        pass

# --------- Historial y helpers ---------
if "history" not in st.session_state:
    st.session_state.history = []
if "_prev_history_len" not in st.session_state:
    st.session_state._prev_history_len = 0
if "pending" not in st.session_state:
    st.session_state.pending = None

def _emit_history():
    for role, msg in st.session_state.history:
        with st.chat_message(role):
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
    # Historial arriba del input
    _emit_history()

    # Ancla para auto-scroll al final del historial
    st.markdown('<div id="history-end"></div>', unsafe_allow_html=True)

    # Si hay una solicitud pendiente, mostramos animación de "pensando" y resolvemos
    if st.session_state.pending:
        with st.chat_message("assistant"):
            with st.spinner("Pensando…"):
                out = ""
                if ONLINE:
                    try:
                        out = run_once(st.session_state.pending)
                    except Exception:
                        out = ""
        if out:
            st.session_state.history.append(("assistant", out))
        st.session_state.pending = None
        # Forzamos rerender para que el mensaje final quede arriba del input
        st.rerun()

    # Input del chat al final (nativo)
    prompt = st.chat_input("Escribe tu mensaje")

    # Si se envía, agregamos el mensaje y dejamos pendiente la respuesta para mostrar spinner
    if prompt and prompt.strip():
        st.session_state.history.append(("user", prompt))
        st.session_state.pending = prompt
        st.rerun()

    # Auto-scroll suave solo cuando cambia el largo del historial
    current_len = len(st.session_state.history)
    if current_len > st.session_state._prev_history_len and components is not None:
        components.html(
            """
            <script>
              const el = document.getElementById('history-end');
              if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'end' }); }
            </script>
            """,
            height=0,
        )
    st.session_state._prev_history_len = current_len

status_color = "#16a34a" if ONLINE else "#9ca3af"
status_label = "Online" if ONLINE else "Offline"
st.markdown(
    f'<div class="status-footer"><span class="status-dot" style="background:{status_color}"></span>'
    f'<span style="color:#6b7280;font-size:0.9rem">{status_label}</span></div>',
    unsafe_allow_html=True,
)



