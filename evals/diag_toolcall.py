"""One-shot diagnostic: capture exact tool.call + reply.audio payload shapes.

Streams Kokoro caller audio at realtime speed into a live Voice Agent session
and prints the verbatim JSON of the first tool.call event.

Logs to /tmp/diag.log. Requires ASSEMBLYAI_API_KEY (server-side use only).
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
LOG = open("/tmp/diag.log", "a")


def log(*a):
    print(*a, file=LOG, flush=True)
    print(*a, flush=True)


def mint():
    key = os.environ["ASSEMBLYAI_API_KEY"]
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(
                "https://agents.assemblyai.com/v1/token?expires_in_seconds=240",
                headers={"Authorization": "Bearer " + key})
            return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())["token"]
        except Exception as e:
            last = e
            log(f"mint attempt {i} failed: {type(e).__name__}")
            time.sleep(8)
    raise RuntimeError(f"mint failed: {type(last).__name__}")


async def connect_ws():
    """Prefer temp tokens; fall back to the API key in the Authorization header
    (server-side only — browsers must always use temp tokens)."""
    import websockets
    try:
        token = mint()
        log("token ok")
        return await websockets.connect(
            "wss://agents.assemblyai.com/v1/ws?token=" + token,
            max_size=10 * 1024 * 1024, open_timeout=20)
    except RuntimeError as e:
        log(f"{e}; trying API key directly")
        return await websockets.connect(
            "wss://agents.assemblyai.com/v1/ws",
            additional_headers={"Authorization": "Bearer " + os.environ["ASSEMBLYAI_API_KEY"]},
            max_size=10 * 1024 * 1024, open_timeout=20)


async def main():
    from _lib.session_config import build_session_update
    ws = await connect_ws()
    async with ws:
        cfg = build_session_update("", phase=0)
        # Use the FULL production prompt — the tool-firing behavior under test.
        await ws.send(json.dumps({"type": "session.update", "session": cfg}))
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        log("first event:", json.loads(raw).get("type"))
        async for raw in ws:
            if json.loads(raw).get("type") == "session.ready":
                break
        log("session ready; waiting out greeting 5s")
        await asyncio.sleep(5)
        wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/caller_test.wav"
        with wave.open(wav, "rb") as w:
            pcm = w.readframes(w.getnframes())
        chunk = int(24000 * 2 * 0.1)
        t0 = time.time()
        dur = len(pcm) / 24000 / 2
        log(f"streaming {dur:.1f}s audio at realtime")
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"type": "input.audio",
                                      "audio": base64.b64encode(pcm[i:i + chunk]).decode()}))
            target = t0 + (i + chunk) / len(pcm) * dur
            await asyncio.sleep(max(0, target - time.time()))
        log("audio streamed; listening 45s")
        t_end = time.time() + 45
        async for raw in ws:
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "tool.call":
                log("TOOL.CALL VERBATIM:", json.dumps(ev)[:600])
                await ws.send(json.dumps({"type": "tool.result", "call_id": ev["call_id"],
                                          "result": json.dumps({"status": "ok"})}))
            elif t == "transcript.user":
                log("USER SAID:", ev.get("text"))
            elif t == "transcript.agent":
                log("AGENT SAID:", ev.get("text"))
            elif t == "session.error":
                log("SESSION ERROR:", ev.get("message"))
                break
            if time.time() > t_end:
                break
        await ws.send(json.dumps({"type": "session.end"}))
        log("DIAG DONE")


asyncio.run(main())
