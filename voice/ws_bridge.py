import os, json, base64, asyncio, struct, io, wave
from typing import Optional, Any, Dict, cast
from fastapi import FastAPI, WebSocket
from adk_agent.agent import root_agent
from google import genai
from google.genai import types
from dotenv import load_dotenv, find_dotenv
import logging

load_dotenv(find_dotenv())

app = FastAPI()
logger = logging.getLogger("voice.ws")
logging.basicConfig(level=logging.INFO)
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
    # Conecta a la API Live probando modelos en cascada
    live_cm = None
    live = None
    chosen_model = None
    events_q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    pump_task: Optional[asyncio.Task[Any]] = None
    last_media_ts: float | None = None
    try:
        last_err = None
        for candidate in LIVE_MODEL_CANDIDATES:
            try:
                logger.info("Intentando Live model: %s", candidate)
                live_cm = GENAI.aio.live.connect(model=candidate)
                live = await live_cm.__aenter__()
                chosen_model = candidate
                logger.info("Conectado a Live model: %s", candidate)
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

        # Crea el task si detectamos alguna forma de lectura
        if callable(getattr(live, "receive", None)) or hasattr(live, "__aiter__"):
            pump_task = asyncio.create_task(_pump_events())

        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            ev = data.get("event")

            if ev == "start":
                stream_sid = data["start"]["streamSid"]
                logger.info("Stream started: %s (live model=%s)", stream_sid, chosen_model)
                # Saludo inicial para verificar retorno de audio a Twilio
                try:
                    greeting = "Hola, soy tu asesor virtual de seguros. ¿En qué puedo ayudarte hoy?"
                    ulaw_greet = await tts_mulaw_8k(greeting)
                    await ws.send_text(json.dumps({
                        "event":"media","streamSid":stream_sid,
                        "media":{"payload": base64.b64encode(ulaw_greet).decode()}
                    }))
                except Exception as e:
                    logger.warning("No se pudo enviar saludo inicial: %s", e)
                continue

            if ev == "media":
                # Twilio → μ-law 8k base64 → bytes → PCM16 8k → PCM16 16k
                mulaw = base64.b64decode(data["media"]["payload"])
                pcm16_8k = decode_mulaw_to_pcm16(mulaw)
                pcm16_16k = upsample_8k_to_16k(pcm16_8k)
                # Enviar audio PCM16 16k a la sesión Live usando tipos del SDK
                blob = types.Blob(data=pcm16_16k, mime_type="audio/pcm;rate=16000")
                await live.send(input=types.LiveClientRealtimeInput(audio=blob))

                # Heurística: si hubo una pausa larga entre frames, marca fin de segmento de audio
                now = asyncio.get_event_loop().time()
                if last_media_ts is not None and (now - last_media_ts) > 0.8:
                    try:
                        await live.send(input=types.LiveClientRealtimeInput(audio_stream_end=True))
                    except Exception as e:
                        logger.debug("Error enviando audio_stream_end: %s", e)
                last_media_ts = now

                # Drena eventos del pump si hay disponibles y detecta final de transcripción
                # Esto evita bloquear el loop principal
                for _ in range(10):
                    if events_q.empty():
                        break
                    e: Dict[str, Any] = await events_q.get()
                    # Intenta extraer texto final de distintas formas
                    user_text = None
                    try:
                        if isinstance(e, dict):
                            et = e.get("type") or e.get("event")
                            # Caso 1: evento de respuesta completada
                            if et in ("response.completed", "live.response.completed", "transcript.completed"):
                                user_text = (e.get("full_text") or e.get("text") or "").strip()
                                if not user_text:
                                    # otros posibles anidamientos
                                    resp = e.get("response") or {}
                                    user_text = (resp.get("output_text") or resp.get("text") or "").strip()
                            # Caso 2: transcript final explícito
                            if not user_text:
                                tr = e.get("transcript") or {}
                                if isinstance(tr, dict) and tr.get("is_final"):
                                    user_text = (tr.get("text") or tr.get("transcript") or "").strip()
                    except Exception:
                        user_text = None

                    if user_text:
                        # Ejecuta el agente
                        agent_res = await root_agent.run_async(user_text)
                        reply = getattr(agent_res, "output_text", "")
                        if not reply:
                            reply = "Gracias. ¿Podrías repetir o darme más detalles?"
                        logger.info("Agent reply len=%d", len(reply))

                        # TTS μ-law 8k y retorna a Twilio (en frames de ~20ms para mejor calidad)
                        ulaw8k = await tts_mulaw_8k(reply)
                        frame = 160  # 20ms a 8kHz = 160 muestras μ-law (1 byte/muestra)
                        pos = 0
                        total = len(ulaw8k)
                        while pos < total:
                            chunk = ulaw8k[pos:pos+frame]
                            await ws.send_text(json.dumps({
                                "event":"media","streamSid":stream_sid,
                                "media":{"payload": base64.b64encode(chunk).decode()}
                            }))
                            pos += frame
                            # Ceder control para no saturar el socket
                            await asyncio.sleep(0)
                        await ws.send_text(json.dumps({
                            "event":"mark","streamSid":stream_sid,
                            "mark":{"name":"resp_done"}
                        }))
                        break

            if ev == "stop":
                logger.info("Stream stopped: %s", stream_sid)
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
