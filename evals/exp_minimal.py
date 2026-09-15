"""Minimal isolation: single tool, ultra-direct prompt. Does ANY tool.call fire?"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
import wave
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)

PROMPT = (
    f"Today is {TODAY.isoformat()} ({TODAY.strftime('%A')}). "
    f"'Tomorrow' means {TOMORROW.isoformat()}. "
    "You are Robin, a dispatcher. The caller will ask for a plumber. "
    "You must call the check_availability tool immediately. Do not speak first. "
    "Do not ask any questions. Just call check_availability with "
    'service="Plumbing" and time_window="morning".'
)

TOOL = {
    "type": "function",
    "name": "check_availability",
    "description": "Check available appointment slots. Call this immediately when the caller needs service.",
    "execution_mode": "interactive",
    "parameters": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "enum": ["Plumbing", "HVAC"]},
            "time_window": {"type": "string", "enum": ["morning", "afternoon", "evening", "any"]},
        },
        "required": ["service"],
    },
}


async def main():
    import websockets
    wav = sys.argv[1]
    ws = await websockets.connect(
        "wss://agents.assemblyai.com/v1/ws",
        additional_headers={"Authorization": "Bearer " + os.environ["ASSEMBLYAI_API_KEY"]},
        max_size=10 * 1024 * 1024, open_timeout=20)
    async with ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "system_prompt": PROMPT,
            "greeting": "Hello.",
            "output": {"type": "audio", "voice": "eve"},
            "tools": [TOOL]}}))
        async for raw in ws:
            if json.loads(raw).get("type") == "session.ready":
                print("READY", flush=True)
                break
        await asyncio.sleep(3)
        with wave.open(wav, "rb") as w:
            pcm = w.readframes(w.getnframes())
        chunk = int(24000 * 2 * 0.1)
        t0 = time.time()
        dur = len(pcm) / 24000 / 2
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"type": "input.audio",
                                      "audio": base64.b64encode(pcm[i:i + chunk]).decode()}))
            await asyncio.sleep(max(0, t0 + (i + chunk) / len(pcm) * dur - time.time()))
        print("AUDIO SENT", flush=True)
        fired = []
        t_end = time.time() + 30
        async for raw in ws:
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "tool.call":
                fired.append(ev.get("name"))
                print("FIRED:", ev.get("name"), json.dumps(ev.get("arguments"))[:200], flush=True)
                await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"],
                                          "result": json.dumps({"slots": ["tomorrow 8-12"]})}))
            elif t == "transcript.agent":
                print("agent:", (ev.get("text") or "")[:90], flush=True)
            if time.time() > t_end:
                break
        print("FIRED TOTAL:", fired, flush=True)
        await ws.send(json.dumps({"type": "session.end"}))


asyncio.run(main())
