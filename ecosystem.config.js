// PM2 ecosystem: voice services, ADK agents (local), and Streamlit UIs
// Ports:
// - voice-app (FastAPI webhook)        : 9000
// - voice-ws (Twilio WS bridge)        : 9001
// - adk-web (single ADK api_server)    : 3000 (sirve múltiples apps)
// - streamlit-insurance (UI)           : 8501, baseUrlPath=/insurance
// - streamlit-help (UI)                : 8511, baseUrlPath=/help

module.exports = {
  apps: [
    // Voice webhook (TwiML)
    {
      name: "voice-app",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec uvicorn voice.app:app --host 0.0.0.0 --port 9000"
      ],
  env_file: ".env",
  env: { PYTHONUNBUFFERED: "1", PYTHONPATH: ".:./agents" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },

    // Voice WebSocket bridge
    {
      name: "voice-ws",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec uvicorn voice.ws_bridge:app --host 0.0.0.0 --port 9001"
      ],
  env_file: ".env",
  env: { PYTHONUNBUFFERED: "1", PYTHONPATH: ".:./agents" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },

    // ADK API server único (local only), sirve múltiples agentes desde ./agents
    {
      name: "adk-web",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec adk api_server --host 127.0.0.1 --port 3000 agents"
      ],
      env_file: ".env",
      env: { PYTHONUNBUFFERED: "1", PORT: "3000" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },

    // Streamlit Insurance UI
    {
      name: "streamlit-insurance",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec streamlit run web/app.py --server.port=8501 --server.baseUrlPath=/insurance --server.headless=true"
      ],
      env_file: ".env",
      env: {
        PYTHONUNBUFFERED: "1",
        ADK_BASE_URL: "http://127.0.0.1:3000",
        APP_NAME: "insurance_agent"
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },

    // Streamlit Help UI
    {
      name: "streamlit-help",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec streamlit run web/help_app.py --server.port=8511 --server.baseUrlPath=/help --server.headless=true"
      ],
      env_file: ".env",
      env: {
        PYTHONUNBUFFERED: "1",
        ADK_BASE_URL: "http://127.0.0.1:3000",
        APP_NAME: "help_agent"
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    }
  ]
};