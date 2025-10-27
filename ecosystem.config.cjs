module.exports = {
  apps: [
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
      env: { PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },
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
      env: { PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },
    {
      name: "adk-web",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec adk web adk_agent.agent"
      ],
      env_file: ".env",
  env: { PYTHONUNBUFFERED: "1", PORT: "3000" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },
    {
      name: "help-web",
      cwd: ".",
      script: "bash",
      interpreter: "none",
      args: [
        "-lc",
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec adk web agents.help_agent.agent"
      ],
      env_file: ".env",
  env: { PYTHONUNBUFFERED: "1", PORT: "3100" },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    }
    ,
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
        // El Streamlit de insurance hablará con el agente insurance en localhost:3000
        ADK_BASE_URL: "http://127.0.0.1:3000",
        APP_NAME: "insurance_agent"
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    },
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
        // El Streamlit de help hablará con el agente help en localhost:3100
        ADK_BASE_URL: "http://127.0.0.1:3100",
        APP_NAME: "help_agent"
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    }
  ]
};