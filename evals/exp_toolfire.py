"""Experiment: why isn't the model calling tools?

Tests tool-firing with (A) 3 tools + short forceful prompt, (B) 7 tools + short
forceful prompt. Both include today's date. Logs to /tmp/exp.log.
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
import wave
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
LOG = open("/tmp/exp.log", "a")


def log(*a):
    print(*a, file=LOG, flush=True)


TODAY = date.today().isoformat()

SHORT_PROMPT = (
    f"You are Robin, a voice dispatcher for Robin Home Services. Today is {TODAY}. "
    "TOOL RULES (follow exactly): "
    "1. When the caller mentions needing a visit, repair, or service, you MUST call "
    "check_availability immediately with service and time_window. Do NOT ask questions first. "
    "2. When the caller asks about hours, pricing, or services, you MUST call lookup_faq. "
    "3. When the caller reports an emergency (burst pipe, gas smell, sparks), you MUST call "
    "escalate_to_human immediately. "
    "Never invent slots, prices, or statuses. Keep replies to one short sentence."
)


def tool_defs(names):
    from _lib.session_config import PHASE_0_TOOLS
    return [t for t in PHASE_0_TOOLS if t["name"] in names]


async def run_exp(label, tools, wav):
    import websockets
    log(f"=== {label}: {len(tools)} tools ===")
    # connect with API key directly (token endpoint flaky)
    ws = await websockets.connect(
        "wss://agents.assemblyai.com/v1/ws",
        additional_headers={"Authorization": "Bearer " + os.environ["ASSEMBLYAI_API_KEY"]},
        max_size=10 * 1024 * 1024, open_timeout=20)
    async with ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "system_prompt": SHORT_PROMPT,
            "greeting": "Thanks for calling Robin Home Services, how can I help?",
            "output": {"type": "audio", "voice": "eve"},
            "tools": tools}}))
        async for raw in ws:
            if json.loads(raw).get("type") == "session.ready":
                break
        await asyncio.sleep(4)
        with wave.open(wav, "rb") as w:
            pcm = w.readframes(w.getnframes())
        chunk = int(24000 * 2 * 0.1)
        t0 = time.time()
        dur = len(pcm) / 24000 / 2
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"type": "input.audio",
                                      "audio": base64.b64encode(pcm[i:i + chunk]).decode()}))
            await asyncio.sleep(max(0, t0 + (i + chunk) / len(pcm) * dur - time.time()))
        fired = []
        t_end = time.time() + 35
        async for raw in ws:
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "tool.call":
                fired.append(ev.get("name"))
                log(f"  TOOL FIRED: {ev.get('name')} args={json.dumps(ev.get('arguments'))[:150]}")
                await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"],
                                          "result": json.dumps({"status": "ok"})}))
            elif t == "transcript.agent":
                log(f"  agent: {ev.get('text')[:100]}")
            if time.time() > t_end or (fired and t == "reply.done"):
                break
        await ws.send(json.dumps({"type": "session.end"}))
        log(f"=== {label}: fired={fired} ===")
        return fired


async def main():
    wav = sys.argv[1]
    a = await run_exp("A-3tools", tool_defs(["check_availability", "lookup_faq", "escalate_to_human"]), wav)
    b = await run_exp("B-7tools", tool_defs([
        "get_caller_profile", "check_availability", "check_job_status", "lookup_faq",
        "create_service_request", "escalate_to_human", "respond_freely"]), wav)
    log(f"RESULT A={a} B={b}")


asyncio.run(main())
