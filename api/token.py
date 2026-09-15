"""GET /api/token — mint a short-lived AssemblyAI Voice Agent token.

The browser uses this token (single-use, expires in ~60s) to open
wss://agents.assemblyai.com/v1/ws?token=... directly. The server API key
never reaches the browser. (Docs: GET https://agents.assemblyai.com/v1/token)
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib.http import Handler  # noqa: E402


class handler(Handler):
    def do_GET(self):
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            self.send_json({"error": "voice backend not configured",
                            "hint": "Set ASSEMBLYAI_API_KEY on the server."}, 503)
            return
        try:
            req = urllib.request.Request(
                "https://agents.assemblyai.com/v1/token?expires_in_seconds=120",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            self.send_json({"error": f"token mint failed: {type(e).__name__}"}, 502)
            return
        token = data.get("token") or data.get("access_token")
        if not token:
            self.send_json({"error": "unexpected token response"}, 502)
            return
        self.send_json({"token": token, "expires_in_seconds": data.get("expires_in_seconds", 120)})
