from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

root_agent = Agent(
    name="AgenteSeguros",
    model="gemini-2.0-flash",
    description="Asesor de seguros conectado a PostgreSQL y pgvector.",
    instruction=(
        "Eres un asesor de ventas de seguros; tu finalidad es vender. Puedes buscar clientes, pólizas y coberturas via MCP"
        "y calcular cotizaciones con la herramienta 'calcular_cotizacion'; obten la información necesaria para convencer al cliente."
    ),
    tools=[
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command="python",
                    args=["-m", "mcp_server.server"],
                    env={
                        "DB_DSN": "postgres://admin-csc-user:admin123!@192.168.1.33:5432/postgres",
                        "RAG_TOPK": "5"
                    }
                )
            )
        )
    ],
)
