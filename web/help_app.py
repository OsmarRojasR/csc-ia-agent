import json
import uuid
import requests
import streamlit as st

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
st.set_page_config(page_title="Agentic - Ayuda y Apoyo")
st.subheader("Agentic - Ayuda y Apoyo")

# Session ID nuevo por sesión (reload de navegador = nueva sesión)
if "session_id" not in st.session_state:
    st.session_state.session_id = f"s_{uuid.uuid4().hex[:8]}"
session_id = st.session_state.session_id

# Crear/actualizar la sesión automáticamente
try:
    ensure_session(BASE_URL, APP_NAME, USER_ID, session_id, state=None)
except Exception as e:
    st.warning(f"No se pudo preparar la sesión: {e}")

# --------- Historial y helpers ---------
if "history" not in st.session_state:
    st.session_state.history = []

def _emit_history():
    for role, msg in st.session_state.history:
        with st.chat_message(role):
            st.markdown(msg)

_emit_history()

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

# --------------- Chat ---------------
prompt = st.chat_input("Escribe tu mensaje")

if prompt:
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            out = run_once(prompt)
        except requests.HTTPError as e:
            out = f"HTTP {e.response.status_code}: {e.response.text}"
        except Exception as e:
            out = str(e)
        st.markdown(out or "(sin texto)")
        st.session_state.history.append(("assistant", out or ""))
