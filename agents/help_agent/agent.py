import os
import sys
from dotenv import load_dotenv, find_dotenv
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Asegura variables de entorno (GOOGLE_API_KEY, etc.)
load_dotenv(find_dotenv())

root_agent = Agent(
    name="HelpMujeres",
    model="gemini-2.0-flash",
    description="Agente de apoyo y asesoría para mujeres víctimas de violencia.",
    instruction=(
        "Eres un agente de apoyo, empático y no juzgador. Tu prioridad es la seguridad de la usuaria. "
        "Antes de dar recomendaciones, valida si hay riesgo inmediato y sugiere llamar al 911 u otra línea de emergencia. "
        "Explica opciones de protección y denuncia con claridad, paso a paso, y ofrece recursos y contactos confiables. "
        "Puedes usar tus herramientas MCP para buscar protocolos, contactos de emergencia y sugerir un plan de seguridad. "
        "No des consejos legales definitivos; invita a buscar asesoría profesional."
    ),
    tools=[
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "mcp_servers.help_mcp_server.server"],
                    env={
                        # Forward claves necesarias al subproceso MCP
                        "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY", ""),
                        # Desbufferizado para logs inmediatos
                        "PYTHONUNBUFFERED": "1",
                    },
                )
            )
        )
    ],
)

import os
import sys
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
                        # Asegura importación de paquetes locales si el cwd cambia bajo pm2
                        "PYTHONPATH": os.getenv("PYTHONPATH", os.getcwd()),
                        # Desbufferizar para logs inmediatos
                        "PYTHONUNBUFFERED": "1",
                    }
                )
            )
        )
    ],
)
