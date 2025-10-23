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
      env: {
        PYTHONUNBUFFERED: "1"
      },
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
      env: {
        PYTHONUNBUFFERED: "1"
      },
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
        "source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true; exec adk web adk_agent"
      ],
      env_file: ".env",
      env: {
        PYTHONUNBUFFERED: "1"
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z"
    }
  ]
};
