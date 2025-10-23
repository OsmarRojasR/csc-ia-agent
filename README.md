# Agente de Seguros con ADK + MCP + PostgreSQL (pgvector)

## Requisitos
- Python 3.10+
- PostgreSQL con extensión `pgvector`
- Base de datos `seguros` con tablas (`customer`, `policy`, `coverage`, `product_chunk`, `product_doc`)

## Instalación

```bash
git clone <ruta_del_repo>
cd insurance_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Edita `.env` con tus credenciales PostgreSQL y tu clave OpenAI.

## Configurar pgvector

```sql
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE product_chunk ALTER COLUMN embedding TYPE vector(768);
```

## Ejecutar MCP

```bash
python -m mcp_server.server
```

## Ejecutar Agente (modo web)

```bash
adk web adk_agent.agent
```

La interfaz estará disponible en http://localhost:8000

## Integración con Twilio (llamadas de voz)

Este proyecto incluye un webhook de Twilio y un puente WebSocket para recibir audio en tiempo real y conectarlo con el agente (ADK+MCP) y Gemini STT/TTS.

### 1) Variables de entorno

En tu `.env` añade:

```
TWILIO_AUTH_TOKEN=<tu_auth_token_de_twilio>
# URL pública WSS que Twilio usará para enviar audio a tu servidor
TWILIO_STREAM_WSS_URL=wss://<tu-dominio-o-ngrok>/voice-stream
GOOGLE_API_KEY=<tu_clave_google_genai>
```

Nota: `GOOGLE_API_KEY` ya se usa en `tools/embed_client.py` y `twilio/ws_bridge.py`.

### 2) Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3) Arrancar los servicios

Se exponen dos apps FastAPI:
- `twilio/app.py` → Webhook `/voice` que devuelve TwiML `<Connect><Stream>`
- `twilio/ws_bridge.py` → WebSocket `/voice-stream` que recibe audio μ-law 8k de Twilio

Ejecuta ambos en puertos diferentes (dos terminales):

```bash
uvicorn voice.app:app --host 0.0.0.0 --port 9000
uvicorn voice.ws_bridge:app --host 0.0.0.0 --port 9001
```

### 4) Exponer con TLS (recomendado: ngrok)

Twilio requiere HTTPS/WSS públicos. Con ngrok:

```bash
ngrok http 9000   # obtendrás https://<X>.ngrok.io para el webhook /voice
ngrok http 9001   # obtendrás https://<Y>.ngrok.io que se usa como WSS: wss://<Y>.ngrok.io/voice-stream
```

Copia la URL WSS del segundo túnel en `TWILIO_STREAM_WSS_URL` (ej. `wss://<Y>.ngrok.io/voice-stream`).

### 5) Configurar tu número en Twilio

En la consola de Twilio → Phone Numbers → Voice → A Call Comes In:
- Webhook: `POST https://<X>.ngrok.io/voice`

El webhook responderá con TwiML que abrirá el stream WebSocket hacia `TWILIO_STREAM_WSS_URL`.

### 6) Flujo de audio e IA

- Twilio envía audio μ-law 8k → `/voice-stream`
- Se convierte a PCM16 16k y se envía a Gemini Live API (STT texto)
- El texto del usuario se pasa al `root_agent` (ADK+MCP)
- La respuesta del agente se sintetiza con Gemini TTS (μ-law 8k) y se envía de vuelta a Twilio en tiempo real

### 7) Notas

- Asegúrate de que la BD PostgreSQL y el MCP estén operativos para que el agente pueda consultar datos.
- Puedes unificar ambas apps en un mismo dominio con un proxy (Nginx/Caddy) o reestructurando para usar un solo `FastAPI`.
- La validación de firmas de Twilio está habilitada en `/voice`; necesitas `TWILIO_AUTH_TOKEN` correcto.
