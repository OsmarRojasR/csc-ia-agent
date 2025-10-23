from fastapi import FastAPI, Request, Response, HTTPException
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
import os

app = FastAPI()
validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])  # debe estar en .env

# URL pública WSS para el stream de Twilio → nuestro WebSocket
# Ejemplo: wss://<tu-dominio-o-ngrok>/voice-stream
TWILIO_STREAM_WSS_URL = os.environ.get("TWILIO_STREAM_WSS_URL", "wss://TU_DOMINIO/voice-stream")

async def _validate(req: Request):
    # Twilio firma sobre URL + params (GET o POST form)
    sig = req.headers.get("X-Twilio-Signature", "")
    form = {}
    try:
        form = dict((await req.form()))  # Voz usa application/x-www-form-urlencoded
    except Exception:
        pass
    if not validator.validate(str(req.url), form or dict(req.query_params), sig):
        raise HTTPException(403, "invalid signature")

@app.post("/voice")
async def voice(req: Request):
    await _validate(req)
    r = VoiceResponse()
    c = Connect()
    c.stream(url=TWILIO_STREAM_WSS_URL)  # <Connect><Stream>
    r.append(c)
    return Response(str(r), media_type="text/xml")
