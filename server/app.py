"""Local development server for Robin Voice Ops.

Serves the web UI, the /api/* endpoints (same handler code as Vercel),
and the Twilio PSTN bridge — all backed by api/_lib.

Usage:
    cd robin-voice-ops
    .venv/bin/python -m uvicorn server.app:app --port 8000

Env: ASSEMBLYAI_API_KEY (for /api/token), ROBIN_DB_PATH or DATABASE_URL,
     Twilio vars (see .env.example) for the PSTN bridge.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api._lib.http import Handler
from server.twilio_bridge import router as twilio_router

BASE = os.path.join(os.path.dirname(__file__), "..")


class Shim(Handler):
    """Drive a Vercel-style Handler without a real socket."""

    def __init__(self, handler_cls, method, full_path, body: bytes):
        # Do NOT call BaseHTTPRequestHandler.__init__ (needs a socket).
        self.command = method
        self.path = full_path
        # Plain dict: match the exact "Content-Length" casing Handler.read_json uses.
        self.headers = {"Content-Length": str(len(body or b""))}
        self.rfile = io.BytesIO(body or b"")
        self._buf = io.BytesIO()
        self._status = 200
        self._bound = handler_cls
        # Bind the handler class's verb methods onto this instance.
        for verb in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
            fn = getattr(handler_cls, verb, None)
            if fn:
                setattr(self, verb, fn.__get__(self, handler_cls))

    # --- socket-level surface captured in-memory ---
    def send_response(self, code, message=None):
        self._status = code

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self._buf

    def run(self):
        verb = getattr(self, f"do_{self.command}", None)
        if not verb:
            return JSONResponse({"error": "method not allowed"}, status_code=405)
        try:
            verb()
        except BrokenPipeError:
            pass
        raw = self._buf.getvalue()
        try:
            return JSONResponse(json.loads(raw.decode() or "null"), status_code=self._status)
        except Exception:
            return Response(content=raw, status_code=self._status,
                            media_type="application/json")


def load_handler(name: str):
    import importlib.util
    path = os.path.join(BASE, "api", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"api_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


app = FastAPI(title="Robin Voice Ops (local)")

app.include_router(twilio_router)


@app.api_route("/api/{name}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_proxy(name: str, request: Request):
    try:
        handler_cls = load_handler(name)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown endpoint"}, status_code=404)
    body = await request.body()
    full_path = request.url.path
    if request.url.query:
        full_path += "?" + request.url.query
    shim = Shim(handler_cls, request.method, full_path, body)
    return shim.run()


app.mount("/", StaticFiles(directory=os.path.join(BASE, "web"), html=True), name="web")
