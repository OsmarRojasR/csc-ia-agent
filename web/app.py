# app.py
import os
import re
import json
import uuid
import requests
import streamlit as st
from urllib.parse import urlparse, urlunparse

# ---------------- UI ----------------
st.set_page_config(page_title="Agentic - Vendedor de Seguros")
st.subheader("Agentic - Vendedor de Seguros")
# --------- Sidebar (config) ---------
with st.sidebar:
    st.subheader("Servidor ADK")

    def _normalize_url(u: str | None, default_port: int) -> str:
        if not u:
            return f"http://127.0.0.1:{default_port}"
        u = u.strip().rstrip("/")
        if not re.match(r"^https?://", u):
            u = f"http://{u}"
        p = urlparse(u)
        host, sep, port = p.netloc.partition(":")
        # Si no trae puerto explícito y es host local, agregamos el predeterminado
        if not port and host in {"localhost", "127.0.0.1", "::1"}:
            netloc = f"{host}:{default_port}"
            u = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
        return u

    # Preferencia: ENV -> secrets -> default local 3000
    default_env = os.getenv("ADK_BASE_URL")
    try:
        default_secret = st.secrets.get("ADK_BASE_URL", None)  # puede no existir
    except Exception:
        default_secret = None
    _pref = default_env or default_secret or "http://127.0.0.1:3000"

    base_url_input = st.text_input("Base URL", _pref)
    # Normalizamos para asegurar puerto 3000 si falta
    base_url = _normalize_url(base_url_input, default_port=3000)
    bearer   = st.text_input("Bearer token (opcional)", os.getenv("ADK_BEARER_TOKEN", ""), type="password")
    streaming = False

    def _headers(sse: bool = False) -> dict:
        h = {"Content-Type": "application/json"}
        if sse:
            h["Accept"] = "text/event-stream"
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        return h

    @st.cache_data(show_spinner=False)
    def list_apps(url: str, auth: str):
        r = requests.get(f"{url}/list-apps", headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    apps = []
    try:
        apps = list_apps(base_url, bearer)
    except Exception as e:
        st.warning(f"No se pudo listar apps: {e}")

    # Selección de app por defecto: APP_NAME env/secrets -> "insurance_agent"
    try:
        secret_app = st.secrets.get("APP_NAME", None)
    except Exception:
        secret_app = None
    app_pref = os.getenv("APP_NAME") or secret_app or "insurance_agent"
    options = apps or [app_pref]
    if app_pref not in options:
        options = [app_pref] + [a for a in options if a != app_pref]
    try:
        default_index = options.index(app_pref)
    except Exception:
        default_index = 0
    app_name = st.selectbox("App", options, index=default_index)

    st.subheader("Sesión")
    user_id = st.text_input("User ID", os.getenv("ADK_USER_ID", "u_123"))

    # session_id persistente
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"s_{uuid.uuid4().hex[:8]}"
    col1, col2 = st.columns([3, 1])
    with col1:
        session_id = st.text_input("Session ID", st.session_state.session_id)
    with col2:
        if st.button("Nuevo ID"):
            st.session_state.session_id = f"s_{uuid.uuid4().hex[:8]}"
            session_id = st.session_state.session_id

    state_json = st.text_area("State JSON (opcional)", "", height=100)

    def ensure_session(url: str, app: str, user: str, sid: str, state: dict | None):
        get_url = f"{url}/apps/{app}/users/{user}/sessions/{sid}"
        # 1) Si ya existe, úsala
        r = requests.get(get_url, headers=_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
        # 2) Si no existe, créala
        payload = {"state": state or {}}
        r = requests.post(get_url, json=payload, headers=_headers(), timeout=15)
        if r.status_code == 200:
            return r.json()
        # 3) Algunos builds devuelven 409 si ya existe
        if r.status_code == 409:
            r2 = requests.get(get_url, headers=_headers(), timeout=10)
            r2.raise_for_status()
            return r2.json()
        r.raise_for_status()

    if st.button("Crear/actualizar sesión"):
        try:
            parsed_state = json.loads(state_json) if state_json.strip() else {}
        except Exception as e:
            st.error(f"State inválido: {e}")
            parsed_state = {}
        try:
            ensure_session(base_url, app_name, user_id, session_id, parsed_state)
            st.session_state["session_ready"] = True
            st.success("Sesión lista")
        except requests.HTTPError as e:
            st.error(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            st.error(str(e))

# ---- Auto-crear sesión al cargar la página (una vez por sesión) ----
if not st.session_state.get("session_ready"):
    try:
        ensure_session(base_url, app_name, user_id, session_id, state=None)
        st.session_state["session_ready"] = True
        # Marca discreta en el sidebar
        with st.sidebar:
            st.caption("Sesión creada automáticamente")
    except Exception as e:
        with st.sidebar:
            st.caption(f"No se pudo crear sesión: {e}")

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
    url = f"{base_url}/run"
    payload = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": text}]},
    }
    r = requests.post(url, json=payload, headers=_headers(), timeout=120)
    r.raise_for_status()
    return _parse_events(r.json())

def run_stream(text: str):
    url = f"{base_url}/run_sse"
    payload = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": text}]},
        "streaming": True,  # token-level
    }
    with requests.post(url, json=payload, headers=_headers(sse=True),
                       stream=True, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                evt = json.loads(data)
                content = evt.get("content") or {}
                for part in content.get("parts") or []:
                    t = part.get("text")
                    if t:
                        yield t
            except Exception:
                continue

# --------------- Chat ---------------
prompt = st.chat_input("Escribe tu mensaje")

if prompt:
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    # asegura la sesión una sola vez si no existe
    if not st.session_state.get("session_ready"):
        try:
            ensure_session(base_url, app_name, user_id, session_id, state=None)
            st.session_state["session_ready"] = True
        except Exception as e:
            with st.chat_message("assistant"):
                st.error(f"Error preparando la sesión: {e}")
            st.stop()

    with st.chat_message("assistant"):
        if streaming:
            ph = st.empty()
            acc = ""
            try:
                for token in run_stream(prompt):
                    acc += token
                    ph.markdown(acc)
            except requests.HTTPError as e:
                msg = f"HTTP {e.response.status_code}: {e.response.text}"
                ph.markdown(msg)
                acc = msg
            except Exception as e:
                acc = str(e)
                ph.markdown(acc)
            st.session_state.history.append(("assistant", acc))
        else:
            try:
                out = run_once(prompt)
            except requests.HTTPError as e:
                out = f"HTTP {e.response.status_code}: {e.response.text}"
            except Exception as e:
                out = str(e)
            st.markdown(out or "(sin texto)")
            st.session_state.history.append(("assistant", out or ""))
