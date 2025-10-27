import os
import sys
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv, find_dotenv
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Asegura variables de entorno desde .env antes de construir el agente
load_dotenv(find_dotenv())

def _build_db_dsn() -> str:
    # Si viene ya armado, úsalo
    dsn = os.getenv("DB_DSN", "").strip()
    if dsn:
        return dsn
    host = os.getenv("PG_HOST", "localhost").strip()
    port = os.getenv("PG_PORT", "5432").strip()
    user = os.getenv("PG_USER", "postgres").strip()
    pwd = os.getenv("PG_PASSWORD", "").strip()
    db  = os.getenv("PG_DATABASE", "postgres").strip()
    # Escapar credenciales por si contienen caracteres especiales o espacios
    user_q = quote_plus(user)
    pwd_q = quote_plus(pwd)
    return f"postgresql://{user_q}:{pwd_q}@{host}:{port}/{db}"

DB_DSN = _build_db_dsn()
RAG_TOPK = os.getenv("RAG_TOPK", "5").strip() or "5"

# Asegura PYTHONPATH absoluto (repo root y ./agents) para subproceso MCP
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_AGENTS_DIR = str(Path(__file__).resolve().parents[1])  # .../agents
_parent_py_path = os.getenv("PYTHONPATH", "").strip()
_py_path_parts = [p for p in _parent_py_path.split(":") if p]
for extra in (_REPO_ROOT, _AGENTS_DIR):
    if extra not in _py_path_parts:
        _py_path_parts.append(extra)
_CHILD_PYTHONPATH = ":".join(_py_path_parts) if _py_path_parts else _REPO_ROOT

root_agent = Agent(
    name="AgenteSeguros",
    model="gemini-2.5-flash",
    description="Asesor de seguros conectado a PostgreSQL y pgvector.",
    instruction=(
        "Eres un asesor de ventas de seguros; tu finalidad es vender. Puedes buscar clientes, pólizas y coberturas via MCP"
        "y calcular cotizaciones con la herramienta 'calcular_cotizacion'; obten la información necesaria para convencer al cliente."
    ),
    tools=[
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "mcp_server.server"],
                    env={
                        "DB_DSN": DB_DSN,
                        "RAG_TOPK": RAG_TOPK,
                        # Forward claves necesarias al subproceso MCP
                        "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY", ""),
                        "GOOGLE_GENAI_USE_VERTEXAI": os.getenv("GOOGLE_GENAI_USE_VERTEXAI", ""),
                        # Asegura importación de paquetes locales con rutas absolutas
                        "PYTHONPATH": _CHILD_PYTHONPATH,
                        # Desbufferizar para logs inmediatos
                        "PYTHONUNBUFFERED": "1",
                    }
                )
            )
        )
    ],
)
