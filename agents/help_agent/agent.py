import os
import sys
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Asegura variables de entorno (GOOGLE_API_KEY, etc.)
load_dotenv(find_dotenv())

# Construye PYTHONPATH robusto para el subproceso MCP (repo root y ./agents)
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_AGENTS_DIR = str(Path(__file__).resolve().parents[1])
_parent_py_path = os.getenv("PYTHONPATH", "").strip()
_py_path_parts = [p for p in _parent_py_path.split(":") if p]
for extra in (_REPO_ROOT, _AGENTS_DIR):
    if extra not in _py_path_parts:
        _py_path_parts.append(extra)
_CHILD_PYTHONPATH = ":".join(_py_path_parts) if _py_path_parts else _REPO_ROOT

root_agent = Agent(
    name="Agente_Apoyo_Mujeres",
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
                        # Asegura importación de paquetes locales
                        "PYTHONPATH": _CHILD_PYTHONPATH,
                        # Desbufferizado para logs inmediatos
                        "PYTHONUNBUFFERED": "1",
                    },
                )
            )
        )
    ],
)
