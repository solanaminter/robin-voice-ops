"""Diag: single-string-param tools. Can the model copy speech into a string arg?"""
import asyncio
import base64
import json
import os
import sys
import time
import wave

PROMPT = """Today is Tuesday, September 15, 2026. You are Robin, a dispatcher.
RULES — your first action MUST be a tool call. Do not speak first.
- If the caller needs a visit, repair, or service: call check_availability. Set the 'request'
  parameter to the caller's exact words describing what they need.
- If the caller reports an emergency: call escalate_to_human. Set 'reason' to the caller's exact words.
- For anything else: call respond_freely with a brief reply."""

TOOLS = [
    {"type": "function", "name": "check_availability", "execution_mode": "interactive",
     "description": "Call when the caller needs a visit, repair, or service.",
     "parameters": {"type": "object",
                    "properties": {"request": {"type": "string",
                        "description": "The caller's exact words describing what they need."}},
                    "required": ["request"]}},
    {"type": "function", "name": "escalate_to_human", "execution_mode": "hold",
     "description": "Call when the caller reports an emergency.",
     "parameters": {"type": "object",
                    "properties": {"reason": {"type": "string",
                        "description": "The caller's exact words describing the emergency."}},
                    "required": ["reason"]}},
    {"type": "function", "name": "respond_freely", "execution_mode": "interactive",
     "description": "Call for anything else.",
     "parameters": {"type": "object",
                    "properties": {"message": {"type": "string"}}},
     "required": ["message"]},
]


async def run_case(wav, label):
    import websockets
    print(f"--- {label} ---", flush=True)
    with wave.open(wav, "rb") as w:
        pcm = w.readframes(w.getnframes())
    ws = await websockets.connect(
        "wss://agents.assemblyai.com/v1/ws",
        additional_headers={"Authorization": "Bearer " + os.environ["ASSEMBLYAI_API_KEY"]},
        max_size=10 * 1024 * 1024, open_timeout=20)
    async with ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "system_prompt": PROMPT, "greeting": "Hello.",
            "output": {"type": "audio", "voice": "eve"}, "tools": TOOLS}}))
        async for raw in ws:
            if json.loads(raw).get("type") == "session.ready":
                break
        await asyncio.sleep(3)
        chunk = int(24000 * 2 * 0.1)
        dur = len(pcm) / 24000 / 2
        t0 = time.time()
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"type": "input.audio",
                                      "audio": base64.b64encode(pcm[i:i + chunk]).decode()}))
            await asyncio.sleep(max(0, t0 + (i + chunk) / len(pcm) * dur - time.time()))
        fired = []
        t_end = time.time() + 30
        while time.time() < t_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "tool.call":
                fired.append(ev["name"])
                print(f"  TOOL: {ev['name']} args={json.dumps(ev.get('arguments'))[:180]}", flush=True)
                await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"],
                                          "result": json.dumps({"status": "ok"})}))
            elif t == "transcript.agent":
                print(f"  AGENT: {(ev.get('text') or '')[:90]}", flush=True)
            if fired:
                break
        print(f"  FIRED: {fired}", flush=True)
        await ws.send(json.dumps({"type": "session.end"}))
        return fired


async def main():
    await run_case("/tmp/caller_test.wav", "book_flow")
    await run_case("/tmp/caller_emg.wav", "emergency")


asyncio.run(main())
