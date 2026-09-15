"""Live evals for Robin Voice Ops — run against the real AssemblyAI Voice Agent API.

Each scenario synthesizes caller audio with a local free TTS (Kokoro via the
hyperframes CLI, or espeak as fallback), streams it over the Voice Agent
WebSocket, and asserts the agent calls the right tools with valid arguments.

Metrics published: p50/p95 turn latency (end-of-user-speech -> first agent
audio), tool-call accuracy, task completion rate.

Requires: ASSEMBLYAI_API_KEY in the environment (never hardcoded, never logged).
Skips gracefully when the key is absent.

Usage:
    ASSEMBLYAI_API_KEY=... .venv/bin/python evals/run_evals.py [--quick]
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
WS_URL = "wss://agents.assemblyai.com/v1/ws"

SCENARIOS = [
    {"id": "book_flow", "wav": "/tmp/caller_test.wav",
     "text": "Hi, I need a plumber tomorrow morning for a leaking water heater.",
     "expect_tools": ["check_availability"],
     "expect_arg_contains": {"request": "plumber"},
     "desc": "Availability lookup fires with caller's words"},
    {"id": "job_status", "wav": "/tmp/caller_job.wav",
     "text": "Hi, where is my technician? My number is 415 555 0101.",
     "expect_tools": ["check_job_status"],
     "expect_arg_contains": {"identifier": "415"},
     "desc": "Job status lookup fires with phone digits in identifier"},
    {"id": "faq_hours", "wav": "/tmp/caller_hours.wav",
     "text": "What are your business hours?",
     "expect_tools": ["lookup_faq"],
     "expect_arg_contains": {"query": "hours"},
     "desc": "FAQ lookup fires for business-hours question"},
    {"id": "faq_price", "wav": "/tmp/caller_price.wav",
     "text": "How much does a visit cost?",
     "expect_tools": ["lookup_faq"],
     "expect_arg_contains": {"query": "cost"},
     "desc": "FAQ lookup fires for pricing question"},
    {"id": "emergency", "wav": "/tmp/caller_emg.wav",
     "text": "Emergency! A pipe burst in my basement, water everywhere!",
     "expect_tools": ["escalate_to_human"],
     # Semantic check: the reason must capture the emergency's content
     # (the model quotes/paraphrases the caller's words; any of these proves it).
     "expect_arg_contains": {"reason": ["pipe", "burst", "water", "flood", "basement", "emergency"]},
     "desc": "Emergency escalates to a human"},
    {"id": "off_script", "wav": "/tmp/caller_off2.wav",
     "text": "What was your name again?",
     "expect_tools": [],
     "expect_arg_contains": {},
     "require_speech": True,
     "desc": "Off-script remark handled gracefully (tool or direct reply)"},
]


def synth_wav(text: str, path: str) -> bool:
    """Synthesize caller audio locally. Kokoro first (free), espeak fallback."""
    import shutil
    venv_py = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")
    env = dict(os.environ)
    if os.path.exists(venv_py):
        env["HYPERFRAMES_PYTHON"] = os.path.abspath(venv_py)
    # Try hyperframes Kokoro TTS (local, free).
    try:
        r = subprocess.run(["npx", "hyperframes", "tts", text, "-o", path],
                           capture_output=True, timeout=180, env=env)
        if r.returncode == 0 and os.path.exists(path):
            return True
    except Exception:
        pass
    try:
        r = subprocess.run(["espeak", "-v", "en", "-s", "150", "-w", path, text],
                           capture_output=True, timeout=60)
        return r.returncode == 0 and os.path.exists(path)
    except Exception:
        return False


def wav_to_pcm16_24k(path: str) -> bytes:
    import audioop
    with wave.open(path, "rb") as w:
        n, raw, sr, ch = w.getnframes(), w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
        sw = w.getsampwidth()
    if ch == 2:
        raw = audioop.tomono(raw, sw, 1, 1)
    if sw != 2:
        raw = audioop.lin2lin(raw, sw, 2)
    if sr != 24000:
        raw, _ = audioop.ratecv(raw, 2, 1, sr, 24000, None)
    return raw


async def run_scenario(scn: dict, api_base: str) -> dict:
    """Drive one scenario: stream pre-synthesized caller audio, collect events, assert tools.

    Uses the production session config (build_session_update) so evals measure what ships.
    Connects with the API key directly (the /v1/token endpoint is intermittently flaky).
    """
    import websockets
    from _lib.session_config import build_session_update
    t0 = time.time()
    wav_path = scn["wav"]
    if not os.path.exists(wav_path):
        # Synthesize from the scenario's caller text so evals are portable —
        # no reliance on pre-existing /tmp files.
        text = scn.get("text")
        if not text or not synth_wav(text, wav_path):
            return {"id": scn["id"], "skipped": True, "reason": f"missing {wav_path}"}
    pcm = wav_to_pcm16_24k(wav_path)

    session = build_session_update("", phase=0)

    tools_called: list[str] = []
    tool_args: list[dict] = []
    latencies: list[float] = []
    agent_texts: list[str] = []
    pending_results: list[tuple[str, str]] = []
    audio_done_at: float | None = None
    latency_measured = False

    try:
        ws = await websockets.connect(
            WS_URL, additional_headers={"Authorization": f"Bearer {API_KEY}"},
            max_size=10 * 1024 * 1024, open_timeout=20)
    except Exception as e:
        return {"id": scn["id"], "error": f"ws_connect: {type(e).__name__}: {e}", "pass": False}
    async with ws:
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        ready = False
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("type") == "session.ready":
                ready = True
                break
            if ev.get("type") == "session.error":
                return {"id": scn["id"], "error": ev.get("message"), "pass": False}
        if not ready:
            return {"id": scn["id"], "error": "no session.ready", "pass": False}
        # Drain the greeting: wait for its reply.done so stale reply.audio
        # events don't pollute the turn-latency measurement.
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=12)
                if json.loads(raw).get("type") == "reply.done":
                    break
        except asyncio.TimeoutError:
            pass
        # Stream caller audio at realtime pace in 100ms chunks.
        chunk = int(24000 * 2 * 0.1)
        dur = len(pcm) / 24000 / 2
        t_start = time.time()
        for i in range(0, len(pcm), chunk):
            await ws.send(json.dumps({"type": "input.audio",
                                      "audio": base64.b64encode(pcm[i:i + chunk]).decode()}))
            await asyncio.sleep(max(0, t_start + (i + chunk) / len(pcm) * dur - time.time()))
        # Listen for up to 40s.
        # Primary latency metric: end of caller audio stream -> first tool.call.
        # (This is the agent's decision latency; reply.audio timing is polluted by
        # greeting drain and chunked delivery, so we don't use it.)
        deadline = time.time() + 40
        audio_done_at = time.time()
        tool_latency_ms: float | None = None
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "tool.call":
                if tool_latency_ms is None:
                    tool_latency_ms = (time.time() - audio_done_at) * 1000
                    latencies.append(tool_latency_ms)
                tools_called.append(ev.get("name"))
                tool_args.append(ev.get("arguments"))
                pending_results.append((ev["call_id"], ev.get("name")))
            elif t == "reply.done" and ev.get("status") != "interrupted":
                for call_id, name in pending_results:
                    await ws.send(json.dumps({"type": "tool.result", "call_id": call_id,
                                              "result": json.dumps({"status": "ok", "eval": True})}))
                pending_results = []
            elif t == "transcript.agent":
                agent_texts.append(ev.get("text", ""))
            if tools_called and len(agent_texts) >= 2:
                break
        try:
            await ws.send(json.dumps({"type": "session.end"}))
        except Exception:
            pass

    expected = set(scn["expect_tools"])
    got = set(tools_called)
    ok = expected <= got if expected else True
    # Validate verbatim-copy args: expected substrings must appear in the tool args.
    arg_ok = True
    for key, substrs in (scn.get("expect_arg_contains") or {}).items():
        if isinstance(substrs, str):
            substrs = [substrs]  # single substring keeps the old strict behavior
        hit = any(any(s.lower() in json.dumps(a.get(key, "")).lower() for s in substrs)
                  for a in tool_args if isinstance(a, dict))
        if not hit:
            arg_ok = False
    ok = ok and arg_ok
    # Off-script: passing means the agent spoke (via tool or directly), without errors.
    if scn.get("require_speech"):
        ok = ok and len(agent_texts) > 0
    return {"id": scn["id"], "desc": scn["desc"], "tools_called": tools_called,
            "tool_args": tool_args, "expected": sorted(expected), "pass": ok,
            "arg_check": arg_ok,
            "latency_ms": latencies, "agent_said": agent_texts[:2],
            "wall_s": round(time.time() - t0, 1)}


async def main():
    quick = "--quick" in sys.argv
    if not API_KEY:
        print("SKIP: ASSEMBLYAI_API_KEY not set — evals need the live API.")
        return 0
    scenarios = SCENARIOS[:3] if quick else SCENARIOS
    results = []
    for scn in scenarios:
        print(f"--- {scn['id']}: {scn['desc']}", flush=True)
        try:
            r = await run_scenario(scn, "")
        except Exception as e:
            r = {"id": scn["id"], "error": f"{type(e).__name__}: {e}", "pass": False}
        results.append(r)
        print(f"    pass={r.get('pass')} tools={r.get('tools_called')} "
              f"lat={r.get('latency_ms')}", flush=True)

    lats = [l for r in results for l in (r.get("latency_ms") or [])]
    passed = sum(1 for r in results if r.get("pass"))
    summary = {
        "n_scenarios": len(results), "n_passed": passed,
        "task_completion": f"{passed}/{len(results)}",
        "p50_ms": round(statistics.median(lats)) if lats else None,
        "p95_ms": round(sorted(lats)[min(len(lats) - 1, int(0.95 * len(lats)))]) if lats else None,
        "n_turns_measured": len(lats),
        "results": results,
        "latency_def": "end-of-caller-audio-stream -> first tool.call (agent decision latency)",
        "note": "Live evals vs the real AssemblyAI Voice Agent API; caller audio synthesized locally (Kokoro/espeak).",
    }
    outdir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "latest.json"), "w") as f:
        json.dump(summary, f, indent=2)
    # Deck + README consume this.
    web_eval = os.path.join(os.path.dirname(__file__), "..", "web", "evals.json")
    with open(web_eval, "w") as f:
        json.dump({"p50_ms": summary["p50_ms"], "p95_ms": summary["p95_ms"],
                   "task_completion": summary["task_completion"]}, f)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
