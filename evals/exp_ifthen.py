"""Diag: IF-THEN script-style prompt with 5 tools. Tests book_flow + job_status + emergency."""
import asyncio
import base64
import json
import os
import sys
import time
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from _lib.session_config import PHASE_0_TOOLS

PROMPT = """Today is Tuesday, September 15, 2026. Tomorrow is 2026-09-16.
You are Robin, a voice dispatcher. You NEVER speak before calling a tool.

Follow these rules in order. The FIRST rule that matches the caller's words wins.
Your first action MUST be the tool call named in that rule. Do not greet, confirm, or explain first.

RULE 1: If the caller mentions needing a visit, a repair, an installation, or service of any kind
(plumber, plumbing, leak, water heater, HVAC, AC, furnace, electrician, wiring, fix, broken, install),
call check_availability with the service they named and the time window they named.
RULE 2: If the caller asks where a technician is, about an appointment or job, or gives a phone number
to look something up, call check_job_status with their phone number.
RULE 3: If the caller asks about business hours, prices, cost, services offered, service area, or warranty,
call lookup_faq with their question.
RULE 4: If the caller says emergency, burst pipe, flooding, gas smell, sparks, or demands a human,
call escalate_to_human with a short reason.
RULE 5: For anything else, call respond_freely with a brief reply.

Convert relative days to YYYY-MM-DD yourself. Never invent slots, prices, or statuses.
Keep spoken replies to one short sentence."""


async def run_case(wav, label, tools, tool_result):
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
            "output": {"type": "audio", "voice": "eve"}, "tools": tools}}))
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
                fired.append((ev["name"], json.dumps(ev.get("arguments"))[:160]))
                print(f"  TOOL: {ev['name']} {json.dumps(ev.get('arguments'))[:160]}", flush=True)
                await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"],
                                          "result": json.dumps(tool_result)}))
            elif t == "transcript.agent":
                print(f"  AGENT: {(ev.get('text') or '')[:90]}", flush=True)
            if fired:
                break
        print(f"  FIRED: {[f[0] for f in fired]}", flush=True)
        await ws.send(json.dumps({"type": "session.end"}))
        return fired


async def main():
    tools = [t for t in PHASE_0_TOOLS if t["name"] in
             ["check_availability", "check_job_status", "lookup_faq", "escalate_to_human", "respond_freely"]]
    await run_case("/tmp/caller_test.wav", "book_flow", tools, {"slots": [{"slot_id": "s1", "label": "Tue 8-12"}]})
    await run_case("/tmp/caller_job.wav", "job_status", tools, {"job": {"status": "en route"}})
    await run_case("/tmp/caller_emg.wav", "emergency", tools, {"status": "escalated"})


asyncio.run(main())
