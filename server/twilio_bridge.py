"""Twilio PSTN bridge for Robin Voice Ops.

Inbound call -> TwiML <Stream> -> this WebSocket -> AssemblyAI Voice Agent API.

Audio path (no guessed telephony fields — the bridge normalizes to the
documented 24 kHz PCM16 browser format):
  Twilio (mulaw 8kHz) --ulaw2lin+upsample--> AssemblyAI input.audio (PCM16 24kHz)
  AssemblyAI reply.audio (PCM16 24kHz) --downsample+lin2ulaw--> Twilio (mulaw 8kHz)

Tool calls from the voice agent are executed against the same api/_lib
backend as the browser demo, so approvals/audit behave identically.

Status: code-complete. Live PSTN verification needs Twilio credentials and a
phone number, which the user has not provided yet — the bridge is NOT wired
to any live number. See README "Phone line (Twilio)".

Env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN (for request validation),
     TWILIO_PHONE_NUMBER, PUBLIC_BASE_URL (https URL of this server),
     ASSEMBLYAI_API_KEY, DATABASE_URL / ROBIN_DB_PATH.
"""
import asyncio
try:
    import audioop  # removed in Python 3.13; PSTN audio path only
except ImportError:  # pragma: no cover
    audioop = None
import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from _lib import approvals, tools
from _lib.session_config import build_session_update, BOOK_TOOL
from _lib.store import get_db

router = APIRouter()

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


@router.post("/voice")
async def voice_webhook(request: Request):
    """Twilio hits this when the number is called. Returns TwiML."""
    # TODO: validate X-Twilio-Signature with TWILIO_TOKEN when credentials exist.
    ws_url = PUBLIC_BASE.replace("https://", "wss://").replace("http://", "ws://")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}/media-stream" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


def mulaw8k_to_pcm16_24k(b: bytes) -> bytes:
    pcm8 = audioop.ulaw2lin(b, 2)
    pcm24, _ = audioop.ratecv(pcm8, 2, 1, 8000, 24000, None)
    return pcm24


def pcm16_24k_to_mulaw8k(b: bytes) -> bytes:
    pcm8, _ = audioop.ratecv(b, 2, 1, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8, 2)


def mint_token() -> str:
    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY not set")
    req = urllib.request.Request(
        "https://agents.assemblyai.com/v1/token?expires_in_seconds=600",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["token"]


def execute_tool(db, name: str, args: dict) -> dict:
    """Same tool backend as the browser demo, including approval gates.

    Delegates to api/tool.py's _run so the Twilio path gets the same
    server-side parsing (api/_lib/parse.py) as the browser path.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
    from tool import _run as api_run
    return api_run(db, name, args)


@router.websocket("/media-stream")
async def media_stream(tw: WebSocket):
    await tw.accept()
    db = get_db()
    stream_sid = None
    aai_ws = None
    try:
        import websockets
        token = mint_token()
        aai_ws = await websockets.connect(
            f"wss://agents.assemblyai.com/v1/ws?token={token}", max_size=8 * 1024 * 1024)
        # Phase 0 config; booking tool reveals after availability (same as browser).
        cfg = build_session_update("", phase=0)
        await aai_ws.send(json.dumps({"type": "session.update", "session": cfg}))
        tools_revealed = False

        async def twilio_to_aai():
            nonlocal stream_sid
            try:
                while True:
                    msg = json.loads(await tw.receive_text())
                    ev = msg.get("event")
                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        # Seed caller memory from Twilio caller ID.
                        frm = msg["start"].get("customParameters", {}).get("From", "")
                        if frm:
                            ctx = tools.caller_context_text(db, frm)
                            tools.upsert_caller(db, frm)
                    elif ev == "media":
                        pcm = mulaw8k_to_pcm16_24k(base64.b64decode(msg["media"]["payload"]))
                        # 100ms frames @24kHz
                        frame = 24000 * 2 // 10
                        for i in range(0, len(pcm), frame):
                            await aai_ws.send(json.dumps({
                                "type": "input.audio",
                                "audio": base64.b64encode(pcm[i:i + frame]).decode()}))
                    elif ev == "stop":
                        break
            except WebSocketDisconnect:
                pass

        async def aai_to_twilio():
            nonlocal tools_revealed
            pending_hold = {}
            try:
                async for raw in aai_ws:
                    ev = json.loads(raw)
                    t = ev.get("type")
                    if t == "reply.audio" and stream_sid:
                        mulaw = pcm16_24k_to_mulaw8k(base64.b64decode(ev["audio"]))
                        # Twilio wants ~20ms mulaw frames (160 bytes @8kHz).
                        for i in range(0, len(mulaw), 160):
                            await tw.send_text(json.dumps({
                                "event": "media", "streamSid": stream_sid,
                                "media": {"payload": base64.b64encode(mulaw[i:i + 160]).decode()}}))
                    elif t == "reply.interrupted" and stream_sid:
                        await tw.send_text(json.dumps(
                            {"event": "clear", "streamSid": stream_sid}))
                    elif t == "tool.call":
                        name, cid = ev.get("name"), ev["call_id"]
                        args = ev.get("arguments", ev.get("args", {}))
                        if isinstance(args, str):
                            try:
                                args = json.loads(args or "{}")
                            except Exception:
                                args = {}
                        if name == "check_availability" and not tools_revealed:
                            tools_revealed = True
                            await aai_ws.send(json.dumps({
                                "type": "session.update",
                                "session": {"tools": build_session_update("", phase=1)["tools"]}}))
                        if name in ("book_appointment", "escalate_to_human"):
                            res = execute_tool(db, name, args)
                            pending_hold[cid] = (name, res.get("approval_id"))
                            await aai_ws.send(json.dumps({"type": "reply.create"}))
                        else:
                            res = execute_tool(db, name, args)
                            await aai_ws.send(json.dumps({
                                "type": "tool.result", "call_id": cid,
                                "result": json.dumps(res)}))
                    elif t == "reply.done":
                        # Poll for decided approvals, then deliver final results.
                        for cid, (name, apid) in list(pending_hold.items()):
                            for _ in range(60):  # up to ~2 min hold
                                ap = approvals.get(db, apid)
                                if ap and ap["status"] != "pending":
                                    final = {"status": ap["status"],
                                             "message": "Your request was reviewed by our dispatcher."}
                                    await aai_ws.send(json.dumps({
                                        "type": "tool.result", "call_id": cid,
                                        "result": json.dumps(final)}))
                                    del pending_hold[cid]
                                    break
                                await asyncio.sleep(2)
            except Exception:
                pass

        await asyncio.gather(twilio_to_aai(), aai_to_twilio())
    finally:
        if aai_ws:
            try:
                await aai_ws.send(json.dumps({"type": "session.end"}))
                await aai_ws.close()
            except Exception:
                pass
