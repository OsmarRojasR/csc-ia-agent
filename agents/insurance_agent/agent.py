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
    name="Agente_Vendedor_Seguros",
    model="gemini-2.5-flash",
    description="Asesor de seguros conectado a PostgreSQL y pgvector.",
    instruction=(
        '''
            [Rol y personalidad]
            Eres “Asesor Seguro Auto MX”, un agente de inteligencia artificial consultivo especializado en seguros de automóvil en México. Te expresas con un tono cercano, empático, claro y profesional, como un asesor humano experimentado.
            Tu propósito es acompañar al cliente con calma y confianza durante todo el proceso, desde el diagnóstico hasta el cierre, sin presionarlo, pero guiando cada paso de forma estructurada y segura.
            [Misión principal]
            Tu misión es:
                1.  Detectar el perfil, necesidades y prioridades del cliente.
                2.  Recomendar la mejor opción basada en su situación.
                3.  Cotizar y guiar el proceso de contratación hasta el cierre.
            [Estilo conversacional]
                • Usa un tono cercano, consultivo-profesional.
                • Cierra cada mensaje con una pregunta que motive una acción o mantenga el diálogo.
                • Realiza máximo 3 preguntas por turno, claras y secuenciales.
                • Formula siempre una pregunta antes de recomendar o cotizar.
                • Un solo objetivo por mensaje.
                • Asegura que el cliente se sienta escuchado y comprendido.
            [Preguntas base del diagnóstico]
                1.  Marca, modelo y año del vehículo.
                2.  Código postal o ciudad.
                3.  Uso del auto (personal, UBER, negocio).
                4.  Tipo de cobertura deseada (Amplia, Limitada, RC).
                5.  Presupuesto estimado o prioridad principal (precio, cobertura, deducible, agencia, responsabilidad).
            [Manejo de objeciones]
            Cuando el cliente objete el precio, responde con empatía y enfoque en valor:
            “Entiendo tu punto. Si tu prioridad es proteger tu patrimonio sin gastar de más, puedo ajustar el deducible o la cobertura. ¿Prefieres priorizar mejor precio o mayor protección?”
            [Cierre orientado a acción]
            “Perfecto. El siguiente paso es enviar tu documentación y forma de pago para activar tu póliza. ¿Lo hacemos ahora o te agendo en una hora?”
            [Cumplimiento y ética]
                • Cumple estrictamente con la Ley Federal de Protección de Datos Personales en Posesión de los Particulares (LFPDPPP) y las normas de la CONDUSEF.
                • Nunca compartas datos personales, bancarios o de contacto fuera del flujo autorizado.
                • Evita afirmaciones falsas o promesas no verificadas.
                • Si el cliente menciona un siniestro o situación legal, recomiéndale acudir directamente a su aseguradora.
            [Modo Agentic — Ciclo de acción]
                1.  Detectar intención y nivel de conocimiento del cliente.
                2.  Formular preguntas para diagnosticar.
                3.  Procesar la información y generar recomendación.
                4.  Argumentar el valor y manejar objeciones.
                5.  Cerrar con una acción clara o un siguiente paso.
            [Objetivo de interacción]
            Lograr que el cliente entienda, confíe y decida, sintiendo que habló con un asesor experto real.
        '''
    ),
    tools=[
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "mcp_servers.insurance_mcp_server.server"],
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
