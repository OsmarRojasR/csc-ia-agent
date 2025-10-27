import os, json, base64, audioop, asyncio
from fastapi import FastAPI, WebSocket
from insurance_agent.agent import root_agent
from google import genai

app = FastAPI()
GENAI = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

async def live_session():
    # Sesión Live API para STT (texto). Modelo Live recomendado.
    return await GENAI.aio.live.connect(model="gemini-2.0-flash-live",
                                        response_modalities=["TEXT"])  # STT → texto
async def tts_mulaw_8k(text: str) -> bytes:
    # Gemini TTS con salida mu-law 8k lista para Twilio
    resp = GENAI.models.generate_content(
        model="gemini-2.5-flash-tts",
        contents=text,
        config=genai.types.GenerateContentConfig(
            audio={"format":"MULAW", "sample_rate_hz":8000}
        ),
    )
    return base64.b64decode(resp.output[0].audio.data)

@app.websocket("/voice-stream")
async def voice_stream(ws: WebSocket):
    await ws.accept()
    stream_sid = None
    live = await live_session()

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            ev = data.get("event")

            if ev == "start":
                stream_sid = data["start"]["streamSid"]
                continue

            if ev == "media":
                # Twilio → μ-law 8k base64 → bytes → PCM16 8k → PCM16 16k
                mulaw = base64.b64decode(data["media"]["payload"])
                pcm16_8k = audioop.ulaw2lin(mulaw, 2)
                pcm16_16k, _ = audioop.ratecv(pcm16_8k, 2, 1, 8000, 16000, None)
                await live.send({"type":"input_audio", "audio": pcm16_16k})

                # Drena eventos del Live para obtener texto final
                async for e in live.recv_events(timeout=0):
                    if e.get("type") == "response.completed":
                        user_text = e.get("full_text","").strip()
                        if not user_text:
                            break

                        # Llama a tu agente ADK+MCP
                        agent_res = await root_agent.run_async(user_text)
                        reply = agent_res.output_text

                        # TTS μ-law 8k y retorna a Twilio
                        ulaw8k = await tts_mulaw_8k(reply)
                        await ws.send_text(json.dumps({
                          "event":"media","streamSid":stream_sid,
                          "media":{"payload": base64.b64encode(ulaw8k).decode()}
                        }))
                        await ws.send_text(json.dumps({
                          "event":"mark","streamSid":stream_sid,
                          "mark":{"name":"resp_done"}
                        }))
                        break

            if ev == "stop":
                break

    finally:
        await ws.close()
