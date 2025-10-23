import os, json, base64, asyncio, struct, io, wave, html, time
from typing import Optional, Any, Dict, cast, List
from fastapi import FastAPI, WebSocket
from adk_agent.agent import root_agent
from google import genai
from google.genai import types
from dotenv import load_dotenv, find_dotenv
from twilio.rest import Client as TwilioClient
import logging

load_dotenv(find_dotenv())

app = FastAPI()
logger = logging.getLogger("voice.ws")
# Permite subir/bajar verbosidad sin tocar código
_lvl = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _lvl, logging.INFO))
_api_key = os.environ.get("GOOGLE_API_KEY")
if not _api_key:
    raise RuntimeError("GOOGLE_API_KEY no configurado en .env")
GENAI = genai.Client(api_key=_api_key)
_live_models_env = os.environ.get("GEMINI_LIVE_MODEL", "").strip()
LIVE_MODEL_CANDIDATES = [m.strip() for m in _live_models_env.split(",") if m.strip()] or [
    # Fallbacks comunes; intentaremos en orden
    "gemini-2.5-flash-preview-tts",
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash",
]
TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "Kore")
TWILIO_TTS_MODE = os.environ.get("TWILIO_TTS_MODE", "gemini").strip().lower()  # "twilio" o "gemini"
TWILIO_VOICE = os.environ.get("TWILIO_VOICE", "Polly.Miguel")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_API_KEY_SID = os.environ.get("TWILIO_API_KEY_SID", "").strip()
TWILIO_API_KEY_SECRET = os.environ.get("TWILIO_API_KEY_SECRET", "").strip()
TWILIO_STREAM_WSS_URL = os.environ.get("TWILIO_STREAM_WSS_URL", "").strip()
LOG_FRAMES_EVERY = int(os.environ.get("LOG_FRAMES_EVERY", "50").strip() or 50)

# Memoria de conversación por llamada (callSid)
CALL_MEMORY: Dict[str, List[Dict[str, str]]] = {}

# ------------------------ TTS: PCM24k → μ-law 8k -------------------------
def _linear2ulaw(sample: int) -> int:
    # basado en ITU G.711 μ-law
    BIAS = 0x84
    CLIP = 32635
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample = sample + BIAS
    # calcular exponente
    exponent = 7
    mask = 0x4000
    for exp in range(7, 0, -1):
        if sample & mask:
            exponent = exp
            break
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw

def pcm16_to_mulaw(pcm16: bytes) -> bytes:
    out = bytearray()
    for (s,) in struct.iter_unpack('<h', pcm16):
        out.append(_linear2ulaw(int(s)))
    return bytes(out)

def downsample_24k_to_8k(pcm16_24k: bytes) -> bytes:
    # decimación simple: tomar 1 de cada 3 muestras (mono)
    out = bytearray()
    i = 0
    data = pcm16_24k
    n = len(data)
    while i + 2 <= n:
        # tomar muestra i (2 bytes little-endian)
        out += data[i:i+2]
        i += 6  # saltar 2 muestras
    return bytes(out)

# Conexión Live API se maneja como context manager en el handler
async def tts_mulaw_8k(text: str) -> bytes:
    # Solicita AUDIO y conviértelo a μ-law 8k para Twilio (detectando formato)
    resp = GENAI.models.generate_content(
        model=TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                )
            ),
        ),
    )
    # Extrae bytes de audio (inline_data) y su mime_type si está presente
    try:
        part = resp.candidates[0].content.parts[0]
        blob = getattr(part, 'inline_data', None)  # type: ignore[attr-defined]
        data = getattr(blob, 'data', None)
        mime = getattr(blob, 'mime_type', None)
    except Exception:
        # fallback aproximado a otras formas
        data, mime = getattr(resp, 'text', b''), None
    if isinstance(data, str):
        try:
            data = base64.b64decode(data)
        except Exception:
            data = data.encode('utf-8')

    raw = bytes(data or b"")
    # Si es WAV, parsea cabecera y obtén PCM16 + sample rate real
    pcm16: bytes
    sr: int
    if (mime or '').lower() in ("audio/wav", "audio/x-wav") or (raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'):
        with wave.open(io.BytesIO(raw), 'rb') as wf:
            sr = wf.getframerate()
            sampwidth = wf.getsampwidth()
            nch = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
            # Requiere PCM16 mono; si no, intenta fallback simple
            if sampwidth != 2:
                # convertir a 16-bit truncando/extendiendo (simple y ruidoso, pero mejor que nada)
                if sampwidth == 1:
                    pcm16 = b''.join(struct.pack('<h', (b-128)<<8) for b in frames)
                else:
                    # si 24-bit, toma los 2 bytes menos significativos (asumiendo 3 bytes por muestra)
                    pcm16 = bytearray()
                    for i in range(0, len(frames), 3):
                        # tomar low y mid byte
                        if i+2 <= len(frames):
                            pcm16 += frames[i:i+2]
                    pcm16 = bytes(pcm16)
            else:
                pcm16 = frames
            if nch == 2:
                # mezclar a mono promediando canales (simple, sin audioop)
                mono = bytearray()
                it = struct.iter_unpack('<hh', pcm16)
                for (l, r) in it:
                    m = (l + r) // 2
                    mono += struct.pack('<h', m)
                pcm16 = bytes(mono)
    else:
        # Asumimos PCM lineal 16-bit LE a 24000 Hz por defecto de TTS
        pcm16 = raw
        sr = 24000

    # Downsample genérico a 8000 Hz (decimación si múltiplo, si no, interpolación lineal)
    def downsample_to_8k(pcm: bytes, src_hz: int) -> bytes:
        if src_hz == 8000:
            return pcm
        if src_hz % 8000 == 0:
            factor = src_hz // 8000
            out = bytearray()
            i = 0
            n = len(pcm)
            step = 2 * factor
            while i + 2 <= n:
                out += pcm[i:i+2]
                i += step
            return bytes(out)
    # Interpolación lineal simple
        samples = [s for (s,) in struct.iter_unpack('<h', pcm)]
        out = bytearray()
        ratio = src_hz / 8000.0
        out_len = int(len(samples) / ratio)
        for i in range(out_len):
            src_pos = i * ratio
            j = int(src_pos)
            a = samples[j]
            b = samples[j+1] if j+1 < len(samples) else a
            t = src_pos - j
            val = int(a + (b - a) * t)
            out += struct.pack('<h', val)
        return bytes(out)

    pcm8k = downsample_to_8k(pcm16, sr)
    return pcm16_to_mulaw(pcm8k)

BIAS = 0x84  # 132
def _mulaw_byte_to_linear(b: int) -> int:
    u = (~b) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    t = ((mantissa << 3) + BIAS) << exponent
    sample = t - BIAS
    return -sample if sign else sample

def decode_mulaw_to_pcm16(mulaw: bytes) -> bytes:
    out = bytearray()
    for b in mulaw:
        s = _mulaw_byte_to_linear(b)
        out += struct.pack('<h', max(-32768, min(32767, s)))
    return bytes(out)

def upsample_8k_to_16k(pcm16_8k: bytes) -> bytes:
    # Duplicación simple de muestras (zero-order hold) 8k → 16k
    out = bytearray()
    it = struct.iter_unpack('<h', pcm16_8k)
    for (s,) in it:
        out += struct.pack('<h', s)
        out += struct.pack('<h', s)
    return bytes(out)

@app.websocket("/voice-stream")
async def voice_stream(ws: WebSocket):
    await ws.accept()
    stream_sid = None
    call_sid: Optional[str] = None
    # Conecta a la API Live probando modelos en cascada
    live_cm = None
    live = None
    chosen_model = None
    events_q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    pump_task: Optional[asyncio.Task[Any]] = None
    responder_task: Optional[asyncio.Task[Any]] = None
    last_media_ts: float | None = None
    frame_count: int = 0
    total_rx_bytes: int = 0
    # Helper: construir prompt con historial
    def _build_agent_input(user_text: str, sid: Optional[str]) -> str:
        turns = CALL_MEMORY.get(sid or "", [])
        history = "\n".join(f"{t['role']}: {t['text']}" for t in turns[-8:])
        if history:
            return f"Historial de la llamada hasta ahora:\n{history}\n\nUsuario: {user_text}"
        return user_text

    # Helper: construir cliente Twilio con API Key si está disponible (recomendado) o con Auth Token
    def _get_twilio_client() -> TwilioClient:
        if TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET and TWILIO_ACCOUNT_SID:
            logger.info(
                "Inicializando TwilioClient con API Key (sid=****%s, account=****%s)",
                TWILIO_API_KEY_SID[-6:],
                TWILIO_ACCOUNT_SID[-6:],
            )
            return TwilioClient(TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET, account_sid=TWILIO_ACCOUNT_SID)
        logger.info(
            "Inicializando TwilioClient con Auth Token (account=****%s)",
            (TWILIO_ACCOUNT_SID[-6:] if TWILIO_ACCOUNT_SID else "??????"),
        )
        return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Helper: actualizar TwiML de la llamada para hablar con Twilio <Say> y reconectar el Stream
    async def _twilio_say_and_restream(say_text: str):
        if not call_sid:
            return
        # Requerimos al menos Account SID y (Auth Token o API Key)
        if not TWILIO_ACCOUNT_SID or not (TWILIO_AUTH_TOKEN or (TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET)):
            raise RuntimeError("twilio_credentials_missing")
        if not TWILIO_STREAM_WSS_URL:
            raise RuntimeError("twilio_stream_url_missing")
        safe = html.escape(say_text)
        twiml = f"""
<Response>
  <Say voice="{html.escape(TWILIO_VOICE)}">{safe}</Say>
  <Connect>
    <Stream url="{html.escape(TWILIO_STREAM_WSS_URL)}"/>
  </Connect>
</Response>
""".strip()

        def _update():
            # Intento 1: API Key si está configurada
            last_err: Optional[Exception] = None
            try:
                client = _get_twilio_client()
                client.calls(str(call_sid)).update(twiml=twiml)
                return
            except Exception as e:
                last_err = e
                # Si usamos API Key y falló con 401, reintenta con Auth Token clásico
                try:
                    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
                        logger.info("Retry update con Auth Token clásico (account=****%s)", TWILIO_ACCOUNT_SID[-6:])
                        client2 = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                        client2.calls(str(call_sid)).update(twiml=twiml)
                        return
                except Exception as e2:
                    last_err = e2
            # Si llegamos aquí, ningún método funcionó
            raise last_err or RuntimeError("twilio_update_failed")

        logger.info("Twilio <Say> + re-<Stream> → callSid=%s, voice=%s, chars=%d", call_sid, TWILIO_VOICE, len(say_text))
        await asyncio.to_thread(_update)

    try:
        last_err = None
        for candidate in LIVE_MODEL_CANDIDATES:
            try:
                logger.info("Intentando Live model: %s", candidate)
                live_cm = GENAI.aio.live.connect(model=candidate)
                live = await live_cm.__aenter__()
                chosen_model = candidate
                logger.info("Conectado a Live model: %s (TTS_MODE=%s, TWILIO_VOICE=%s)", candidate, TWILIO_TTS_MODE, TWILIO_VOICE)
                break
            except Exception as e:
                last_err = e
                logger.warning("Fallo conectando con %s: %s", candidate, str(e))
                live_cm = None
                live = None
                continue
        if not live:
            raise RuntimeError(f"No se pudo abrir Live con ninguno de: {', '.join(LIVE_MODEL_CANDIDATES)}; ultimo error: {last_err}")

        # Arranca un pump de eventos si es posible
        async def _pump_events():
            nonlocal live
            try:
                recv = getattr(live, "receive", None)
                if callable(recv):
                    logger.info("Live session soporta receive(); iniciando pump de eventos.")
                    try:
                        agen = recv()
                        # Si devuelve un async generator, iteramos sobre él
                        if hasattr(agen, '__aiter__'):
                            async for ev in agen:
                                await events_q.put(ev)
                        else:
                            # Si fuese un awaitable que entrega un evento, caemos a bucle
                            while True:
                                ev = await agen
                                await events_q.put(ev)
                    except TypeError:
                        # Versión que requiere await recv() cada vez
                        while True:
                            ev = await recv()
                            await events_q.put(ev)
                elif hasattr(live, "__aiter__"):
                    logger.info("Live session es async-iterable; iniciando pump de eventos.")
                    async for ev in live:
                        await events_q.put(ev)
                else:
                    logger.warning("La sesión Live no expone receive()/iter; no se podrá leer eventos.")
            except Exception as e:
                logger.info("Pump de eventos finalizado: %s", e)

        # Task: bombeo de eventos del Live
        if callable(getattr(live, "receive", None)) or hasattr(live, "__aiter__"):
            pump_task = asyncio.create_task(_pump_events())

        # Task: responder cuando detectemos texto final del usuario
        async def _responder():
            nonlocal stream_sid, call_sid
            logged = 0
            while True:
                e: Dict[str, Any] = await events_q.get()
                try:
                    if logged < 10:
                        logger.info("Live event type=%s keys=%s", getattr(e, 'type', e.get('type', None)), list(e.keys()) if isinstance(e, dict) else type(e))
                        logged += 1
                    if not isinstance(e, dict):
                        continue
                    et = e.get("type") or e.get("event")
                    user_text: Optional[str] = None
                    if et in ("response.completed", "live.response.completed", "transcript.completed"):
                        user_text = (e.get("full_text") or e.get("text") or "").strip()
                        if not user_text:
                            resp = cast(Dict[str, Any], e.get("response") or {})
                            user_text = (resp.get("output_text") or resp.get("text") or "").strip()
                    else:
                        tr = cast(Dict[str, Any], e.get("transcript") or {})
                        if isinstance(tr, dict) and tr.get("is_final"):
                            user_text = (tr.get("text") or tr.get("transcript") or "").strip()

                    if user_text:
                        logger.info("ASR final: '%s'%s", user_text[:120], "…" if len(user_text) > 120 else "")
                        # Guardar turno del usuario
                        if call_sid:
                            CALL_MEMORY.setdefault(call_sid, []).append({"role": "user", "text": user_text})
                        agent_input = _build_agent_input(user_text, call_sid)
                        t0 = time.perf_counter()
                        agent_res = await root_agent.run_async(agent_input)
                        t1 = time.perf_counter()
                        reply = getattr(agent_res, "output_text", "") or "Gracias. ¿Podrías repetir o darme más detalles?"
                        logger.info("Agente respondió en %.3fs (len=%d)", t1 - t0, len(reply))

                        # Guardar turno del asistente
                        if call_sid:
                            CALL_MEMORY.setdefault(call_sid, []).append({"role": "assistant", "text": reply})

                        # Si se configura TTS nativo de Twilio, hacemos redirect con <Say> y reconectamos <Stream>
                        if TWILIO_TTS_MODE == "twilio":
                            try:
                                await _twilio_say_and_restream(reply)
                                # Twilio cerrará este stream y abrirá uno nuevo; finalizamos este handler.
                                logger.info("Cerrando WS actual tras <Say> para que Twilio reabra el Stream…")
                                try:
                                    await ws.close()
                                except Exception:
                                    pass
                                return
                            except Exception as tex:
                                logger.warning("Fallo TTS Twilio (%s), fallback a Gemini TTS.", tex)

                        # TTS μ-law 8k en frames de 20ms
                        ulaw8k = await tts_mulaw_8k(reply)
                        frame = 160
                        for pos in range(0, len(ulaw8k), frame):
                            chunk = ulaw8k[pos:pos+frame]
                            await ws.send_text(json.dumps({
                                "event":"media","streamSid":stream_sid,
                                "media":{"payload": base64.b64encode(chunk).decode()}
                            }))
                            await asyncio.sleep(0)
                        await ws.send_text(json.dumps({
                            "event":"mark","streamSid":stream_sid,
                            "mark":{"name":"resp_done"}
                        }))
                    else:
                        logger.debug("Evento Live sin texto final utilizable: %s", et)
                except Exception as ex:
                    logger.warning("Responder task error: %s", ex)

        responder_task = asyncio.create_task(_responder())

        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            ev = data.get("event")

            if ev == "start":
                stream_sid = data["start"]["streamSid"]
                call_sid = data["start"].get("callSid") or data["start"].get("call_sid")
                logger.info("Stream started: %s callSid=%s (live model=%s)", stream_sid, call_sid, chosen_model)
                if call_sid:
                    logger.info("Turnos en memoria para callSid=%s: %d", call_sid, len(CALL_MEMORY.get(call_sid, [])))
                # Saludo inicial
                try:
                    greeting = "Hola, soy tu asesor virtual de seguros. ¿En qué puedo ayudarte hoy?"
                    if TWILIO_TTS_MODE == "twilio":
                        try:
                            await live.send(input=types.LiveClientRealtimeInput(activity_start=types.ActivityStart()))
                        except Exception:
                            pass
                        try:
                            await _twilio_say_and_restream(greeting)
                        except Exception as tex:
                            # Si falla autenticación o permisos, hacemos fallback inmediato a Gemini TTS para no dejar silencio
                            logger.warning("No se pudo enviar saludo con Twilio (<Say>): %s. Fallback a Gemini TTS.", tex)
                            ulaw_greet = await tts_mulaw_8k(greeting)
                            await ws.send_text(json.dumps({
                                "event":"media","streamSid":stream_sid,
                                "media":{"payload": base64.b64encode(ulaw_greet).decode()}
                            }))
                    else:
                        ulaw_greet = await tts_mulaw_8k(greeting)
                        await ws.send_text(json.dumps({
                            "event":"media","streamSid":stream_sid,
                            "media":{"payload": base64.b64encode(ulaw_greet).decode()}
                        }))
                        try:
                            await live.send(input=types.LiveClientRealtimeInput(activity_start=types.ActivityStart()))
                        except Exception as e:
                            logger.debug("No se pudo enviar activity_start: %s", e)
                except Exception as e:
                    logger.warning("No se pudo enviar saludo inicial: %s", e)
                continue

            if ev == "media":
                # Twilio → μ-law 8k base64 → bytes → PCM16 8k (sin cambiar frecuencia)
                mulaw = base64.b64decode(data["media"]["payload"])
                total_rx_bytes += len(mulaw)
                frame_count += 1
                if frame_count % LOG_FRAMES_EVERY == 0:
                    logger.info("Frames recibidos: %d  bytes(mu-law): %d", frame_count, total_rx_bytes)
                pcm16_8k = decode_mulaw_to_pcm16(mulaw)
                # Enviar audio PCM16 8k directo a la sesión Live (sin upsample)
                blob = types.Blob(data=pcm16_8k, mime_type="audio/pcm;rate=8000")
                await live.send(input=types.LiveClientRealtimeInput(audio=blob))

                # Heurística: si hubo una pausa larga entre frames, marca fin de segmento de audio
                now = asyncio.get_event_loop().time()
                if last_media_ts is not None and (now - last_media_ts) > 0.8:
                    try:
                        await live.send(input=types.LiveClientRealtimeInput(audio_stream_end=True, activity_end=types.ActivityEnd()), end_of_turn=True)
                        logger.info("Detectada pausa larga; enviado end_of_turn al Live API")
                    except Exception as e:
                        logger.debug("Error enviando audio_stream_end: %s", e)
                last_media_ts = now

            if ev == "stop":
                logger.info("Stream stopped: %s  frames=%d  bytes=%d", stream_sid, frame_count, total_rx_bytes)
                break
    finally:
        try:
            if live_cm is not None:
                await live_cm.__aexit__(None, None, None)
        finally:
            if pump_task:
                try:
                    pump_task.cancel()
                except Exception:
                    pass
            if responder_task:
                try:
                    responder_task.cancel()
                except Exception:
                    pass
            await ws.close()

@app.get("/health")
async def health():
    return {"status":"ok"}

@app.get("/live-models")
async def list_live_models():
    try:
        models = list(GENAI.models.list())
        names = [getattr(m, "name", str(m)) for m in models]
        return {"models": names}
    except Exception as e:
        return {"error": str(e)}

# Endpoint de diagnóstico: verifica credenciales Twilio haciendo una llamada simple a la API
@app.get("/twilio-auth-check")
async def twilio_auth_check():
    try:
        client = TwilioClient(TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET, account_sid=TWILIO_ACCOUNT_SID) \
                 if (TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET and TWILIO_ACCOUNT_SID) \
                 else TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        # Prueba liviana: obtener la cuenta y listar 1 llamada
        acct = client.api.accounts(TWILIO_ACCOUNT_SID).fetch()
        calls = client.calls.list(limit=1)
        return {
            "ok": True,
            "account_sid": getattr(acct, 'sid', None),
            "using_api_key": bool(TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET),
            "last_call_sid": calls[0].sid if calls else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "account_sid": TWILIO_ACCOUNT_SID[-6:] if TWILIO_ACCOUNT_SID else None}
