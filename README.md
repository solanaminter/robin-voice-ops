# ◉ Robin Voice Ops

**The voice dispatcher for home-services businesses** — built on the [AssemblyAI Voice Agent API](https://www.assemblyai.com/docs/voice-agents). It doesn't just talk: it books appointments, checks job status, files service requests, answers from a knowledge base, and escalates emergencies — with a **human approval gate on every irreversible action** and a **hash-chained audit log** of everything it does.

🎥 Demo video: *(YouTube link — parent to add after upload)*
🚀 Live demo: *(Vercel URL — parent to add after deploy)*
📊 Pitch deck: [/slides.html](/slides.html) (served with the demo)

Built solo by **Solana Minter (Purple Castle Ventures)** for the **AssemblyAI Voice Agent Hackathon** (lablab.ai, September 2026).

---

## The problem

Small home-services contractors (plumbing, HVAC, electrical) miss a large share of calls during job hours — industry call-tracking reports put it as high as ~62%. Every missed call is $200–$500 walking to a competitor; after-hours dispatchers cost $3k+/month. Existing "AI receptionists" are prompt wrappers — they take messages but can't *do* anything.

## What Robin does

| Caller says | Robin does |
|---|---|
| "I need a plumber tomorrow morning" | Looks up **real open slots** → holds the booking for **human approval** → announces the confirmation |
| "Where's my technician?" | Looks up the **live job status** from the jobs DB |
| "What are your hours? How much is a visit?" | Answers from the **knowledge base** |
| "A pipe burst, water everywhere!" | **Escalates to a human** immediately (approval-gated) |
| "It's Maria Lopez" / calling from a known number | **Recognizes the returning caller** from cross-session memory |

**Nothing irreversible happens on voice alone.** Bookings and escalations enter a dispatcher approval queue; the agent waits in *hold mode* until a human approves or rejects, then delivers the outcome back into the conversation. Every tool call, approval request, and decision is written to a SHA-256 **hash-chained audit log** you can verify in the ops dashboard.

## Deep AssemblyAI usage (not a wrapper)

- **Inline `session.update`** with a production system prompt, greeting, keyterms biasing, transcription prompt, near-field `voice_focus`, and barge-in turn detection
- **JSON-Schema function tools** with `execution_mode: "interactive"` (lookups) vs `"hold"` (gated actions), plus `timeout_seconds`
- **Progressive tool reveal** — the `book_appointment` tool is only registered *after* `check_availability` returns real slots, so the model structurally cannot fabricate bookings
- **Parameter hints** (`enum`/`pattern`/`examples`/`format`) so spoken phone numbers validate before tools run
- **Temp-token auth** (`GET /v1/token`) — the API key never reaches the browser
- **`session.resume`** reconnection with the `resume_token` from `session.ready`
- **Word-level agent captions** (`transcript.agent.delta`) and partial user transcripts
- **Twilio PSTN bridge** — a real phone line path (μ-law 8 kHz ↔ 24 kHz PCM16 transcoding), code-complete

## Measured, not adjectives

| Metric | Value |
|---|---|
| Tool-call latency p50 (caller stops → first `tool.call`) | 1.81s (live API, 6 scenarios) |
| Task completion (6 scripted voice scenarios, live API) | 6/6 |
| Offline unit tests | 25/25 |

Evals run against the **live Voice Agent API** with locally synthesized caller audio (Kokoro, free). Harness + raw results: [`evals/`](evals/). Method and honest limitations: [`EVALS.md`](EVALS.md).

**Key design finding:** the model reliably classifies intent and copies caller speech verbatim into string params, but unreliably normalizes speech into enums/formats. So tools take free-text strings ("copy, don't normalize") and all entity extraction runs server-side in `api/_lib/parse.py` — deterministic and unit-tested.

## Architecture

```
Caller (browser mic / Twilio PSTN)
   │  PCM16 24 kHz input.audio / reply.audio (Twilio: μ-law 8 kHz, transcoded)
   ▼
AssemblyAI Voice Agent API  ←── session.update (prompt, tools, keyterms, voice)
   │  tool.call / tool.result (hold mode for gated actions)
   ▼
/api/tool  →  api/_lib/tools.py  →  DB (Postgres / SQLite / memory)
   │  book_appointment, escalate_to_human → approval queue (human decides)
   ▼
Hash-chained audit log  →  dispatcher dashboard (/ tab "Dispatcher ops")
```

## Run it locally

```bash
cd robin-voice-ops
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# optional: local persistence (otherwise in-memory demo mode)
export ROBIN_DB_PATH=./data/robin.db
# optional: live voice (otherwise scripted demo mode)
export ASSEMBLYAI_API_KEY=...
.venv/bin/python -m uvicorn server.app:app --port 8000
# open http://localhost:8000
```

Without `ASSEMBLYAI_API_KEY`, the UI runs a **scripted demo mode** that walks the booking + approval flow end to end.

### Phone line (Twilio) — pending credentials

The bridge is code-complete (`server/twilio_bridge.py`): `POST /voice` returns TwiML, `/media-stream` relays to AssemblyAI with audio transcoding, and tool calls hit the same backend (approvals included). To go live it needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, and `PUBLIC_BASE_URL` — no number is attached yet.

## Deploy (Vercel)

`vercel.json` maps `/` → `web/index.html`, `/api/*` → Python serverless functions. Set `ASSEMBLYAI_API_KEY` (and optionally `DATABASE_URL` for Postgres, e.g. Neon) in the project env.

## Project layout

```
web/            index.html, app.js, styles.css, slides.html (pitch deck)
api/            Vercel serverless functions (thin handlers)
api/_lib/       tools, approvals, audit, db, session_config, store
server/         local FastAPI dev server + Twilio PSTN bridge
tests/          14 offline unit tests (pytest)
evals/          live voice eval harness (Kokoro caller audio → real API)
video/          demo-video build notes
```

## Honest limitations

- The demo business (Robin Home Services) and its jobs/slots are **seeded fictional data** — the side effects are real (DB holds, audit entries), the business isn't.
- Vercel serverless functions are stateless: use `DATABASE_URL` (Postgres) in production; the in-memory fallback is per-instance demo mode (the UI labels it).
- `session.resume` is implemented per the official docs; the live resume handshake still needs a final verification pass (token endpoint was flaky during testing).
- The Twilio bridge is code-complete but has not carried a live PSTN call yet (no credentials/number attached).
- Latency numbers come from 6 scripted scenarios, not a production call distribution.

## License

MIT — see [LICENSE](LICENSE).
