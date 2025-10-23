from fastapi import FastAPI, Request, Response, HTTPException
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect
import os
from dotenv import load_dotenv, find_dotenv
import logging

load_dotenv(find_dotenv())  # Carga variables desde .env

app = FastAPI()

# Creamos el validador solo si existe el token, así no rompe la importación si falta
_twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
validator = RequestValidator(_twilio_token) if _twilio_token else None

# URL pública WSS para el stream de Twilio → nuestro WebSocket
# Ejemplo: wss://<tu-dominio-o-ngrok>/voice-stream
TWILIO_STREAM_WSS_URL = os.environ.get("TWILIO_STREAM_WSS_URL", "wss://TU_DOMINIO/voice-stream")
TWILIO_SKIP_VALIDATE = os.environ.get("TWILIO_SKIP_VALIDATE", "false").lower() in ("1","true","yes")

logger = logging.getLogger("voice.app")
logging.basicConfig(level=logging.INFO)

@app.get("/health")
async def health():
    return {"status":"ok"}

async def _validate(req: Request):
    # Twilio firma sobre URL + params (GET o POST form)
    if TWILIO_SKIP_VALIDATE:
        return
    if not validator:
        raise HTTPException(500, "TWILIO_AUTH_TOKEN no configurado en .env")
    sig = req.headers.get("X-Twilio-Signature", "")
    form = {}
    try:
        form = dict((await req.form()))  # Voz usa application/x-www-form-urlencoded
    except Exception:
        pass
    url = str(req.url)
    if not validator.validate(url, form or dict(req.query_params), sig):
        logger.warning("Twilio signature invalid for URL %s", url)
        raise HTTPException(403, "invalid signature")

@app.post("/voice")
async def voice(req: Request):
    await _validate(req)
    r = VoiceResponse()
    c = Connect()
    c.stream(url=TWILIO_STREAM_WSS_URL)  # <Connect><Stream>
    r.append(c)
    return Response(str(r), media_type="text/xml")

# Ruta de prueba sin validación para verificar TwiML/Stream rápidamente
@app.get("/voice-test")
@app.post("/voice-test")
async def voice_test():
    r = VoiceResponse()
    c = Connect()
    c.stream(url=TWILIO_STREAM_WSS_URL)
    r.append(c)
    return Response(str(r), media_type="text/xml")
