"""Builds the AssemblyAI Voice Agent `session.update` payload.

Deep API usage, per the official docs:
- JSON-Schema tools with parameter hints (enum/pattern/examples/format) so
  spoken values validate before the tool runs and turn-detection waits for
  complete values (e.g. full phone numbers).
- execution_mode: "hold" for gated/sensitive flows (booking, escalation) —
  the agent stays silent until we send tool.result; "interactive" for lookups.
- Progressive tool reveal: phase 0 tools at greeting; book_appointment is only
  revealed after check_availability returns real slots, so the model can never
  fabricate a booking before seeing availability.
- keyterms_prompt biased to the home-services domain; voice_focus for noisy
  environments; transcription_prompt for entity accuracy.
"""
from __future__ import annotations

BUSINESS_NAME = "Robin Home Services"
BUSINESS_PHONE = "(415) 555-0132"

SYSTEM_PROMPT = """You are Robin, the friendly voice dispatcher for Robin Home Services — a plumbing, HVAC, and electrical company serving the Springfield metro area. You answer calls, book appointments, check job status, answer common questions, and escalate to a human dispatcher when needed.

TODAY IS {today_long} ({today}). "Tomorrow" means {tomorrow}. When a caller gives a relative day, convert it to the YYYY-MM-DD date yourself — never ask them to.

PERSONALITY: Warm, efficient, unhurried. You sound like a great office manager, not a robot. Keep replies short — one or two sentences — because this is voice.

CRITICAL RULES:
1. TOOLS FIRST, TALK SECOND. The moment you recognize what the caller needs, CALL THE TOOL immediately — do not speak first, do not ask clarifying questions first.
   - Caller mentions needing a visit, repair, or service → call check_availability at once.
   - Caller asks about hours, pricing, services, warranty → call lookup_faq at once.
   - Caller asks where their technician is or about an existing job → call check_job_status at once.
   - Caller reports an emergency (burst pipe, flooding, gas smell, sparks, no heat in freezing weather) → call escalate_to_human at once.
2. COPY, DON'T NORMALIZE. Every tool parameter is a plain string: put the caller's EXACT WORDS in it. Do not map to categories, do not reformat dates or phone numbers, do not paraphrase. The system parses everything server-side.
   - check_availability: request = exactly what they said they need ("a plumber tomorrow morning for a leaking water heater").
   - check_job_status: identifier = their phone number or job ID exactly as spoken.
   - lookup_faq: query = their question in their words.
   - escalate_to_human: reason = quote the caller's own words about the emergency verbatim (do not paraphrase — the dispatcher needs their exact description).
3. NEVER invent availability, job statuses, prices, or confirmation numbers. If a tool returns no match, say so and offer the next step.
4. Booking and escalation are GATED: the tool creates a request that a human dispatcher must approve. Tell the caller plainly that their request is pending dispatcher approval — do not claim it is confirmed until the tool result says confirmed.
5. Phone numbers: always repeat a phone number back digit-by-digit to confirm before using it.
6. If the caller goes off-script ("are you a real person?", small talk), use respond_freely briefly, then steer back: "Happy to help with that — were you looking to book a visit or check on a job?"
7. IDENTITY: If asked whether you are a real person, a human, or a robot: you are Robin, an AI voice dispatcher for Robin Home Services. Say so plainly and briefly ("I'm Robin, the AI dispatcher for Robin Home Services"), then immediately steer back to helping. Never claim to be human.

FEW-SHOT EXAMPLES (note: every argument is the caller's exact words, copied verbatim):
> User: "My water heater is leaking, can someone come tomorrow morning?"
> You: [call check_availability with request="My water heater is leaking, can someone come tomorrow morning?"] "I found a plumbing slot tomorrow morning, 8 to 12. Want me to request it? It'll need our dispatcher's approval to confirm."
> User: "Yes please, I'm Maria Lopez, 415-555-0101."
> You: [call book_appointment with slot_id="<id from check_availability>", name="Maria Lopez", phone="415-555-0101"] "Request sent — it's pending dispatcher approval. You'll get a confirmation once they approve it."
> User: "Where's my technician? My number is 415 555 0101."
> You: [call check_job_status with identifier="415 555 0101"] "Devon Park is en route to your water heater replacement right now."
> User: "How much is a visit?"
> You: [call lookup_faq with query="How much is a visit?"] "Diagnostic visits are $89, waived if you go ahead with the repair."
> User: "This is an emergency, pipe burst!"
> You: [call escalate_to_human with reason="This is an emergency, pipe burst!"] "That's urgent — I'm flagging a human dispatcher right now. Stay on the line."
"""

KEYTERMS = [
    "Robin Home Services", "HVAC", "water heater", "tankless", "furnace",
    "circuit breaker", "breaker panel", "thermostat", "sump pump",
    "Devon Park", "Priya Nair", "Tom Okafor", "Springfield", "Riverside",
    "Oakdale", "Fairview", "diagnostic visit", "dispatch", "technician",
]

TRANSCRIPTION_PROMPT = (
    "This is a customer service call for a home services company (plumbing, HVAC, electrical). "
    "Expect service names, appointment dates and times, US phone numbers, street addresses, "
    "and technician names. Alphanumeric job IDs look like 'job_7f3a21'."
)

GREETING = ("Thanks for calling Robin Home Services — how can I help?")


def _phone_prop(desc: str) -> dict:
    return {
        "type": "string",
        "description": desc,
        # Spoken digits often arrive with spaces: allow them, we strip server-side.
        "pattern": r"\+?[0-9 ()\-\.]{7,20}",
        "examples": ["+14155550101", "415 555 0101", "(415) 555-0101"],
    }


def _date_prop() -> dict:
    return {"type": "string", "description": "Date as YYYY-MM-DD (e.g. 2026-09-16).",
            "format": "date", "examples": ["2026-09-16", "2026-09-17"]}


def _verbatim(desc: str) -> dict:
    """A free-text string param the model fills by copying caller speech verbatim.

    The Voice Agent LLM reliably copies what it heard into a plain string, but
    unreliably normalizes speech into enums/formatted values. All parsing
    (service, date, time window, phone digits) happens server-side in
    api/_lib/parse.py, where it is deterministic and testable.
    """
    return {"type": "string", "description": desc + " Copy the caller's exact words; do not paraphrase or normalize."}


PHASE_0_TOOLS = [
    {
        "type": "function",
        "name": "get_caller_profile",
        "description": ("Call this when you have the caller's phone number, to check whether "
                        "they are a returning customer and recall their history."),
        "execution_mode": "interactive",
        "parameters": {"type": "object",
                       "properties": {"phone": _verbatim("Caller's phone number, exactly as they said it.")},
                       "required": ["phone"]},
    },
    {
        "type": "function",
        "name": "check_availability",
        "description": ("Call this FIRST when the caller mentions needing a visit, repair, or service, "
                        "or asks when someone can come out. Do not call this to check an existing job — use check_job_status."),
        "execution_mode": "interactive",
        "parameters": {"type": "object",
                       "properties": {
                           "request": _verbatim("What the caller needs and when, in their own words."),
                       },
                       "required": ["request"]},
    },
    {
        "type": "function",
        "name": "check_job_status",
        "description": ("Call this FIRST when the caller asks about an existing job: where is the technician, "
                        "when is my appointment, what is the status. Do not call for new bookings."),
        "execution_mode": "interactive",
        "parameters": {"type": "object",
                       "properties": {
                           "identifier": _verbatim("The caller's phone number or job ID, exactly as they said it."),
                       },
                       "required": ["identifier"]},
    },
    {
        "type": "function",
        "name": "lookup_faq",
        "description": ("Call this FIRST for general business questions: hours, pricing, service area, warranties, "
                        "how long a job takes. Do not call for bookings or job status."),
        "execution_mode": "interactive",
        "parameters": {"type": "object",
                       "properties": {"query": _verbatim("The caller's question.")},
                       "required": ["query"]},
    },
    {
        "type": "function",
        "name": "create_service_request",
        "description": ("Call this when the caller reports a non-urgent issue to log (dripping faucet, noisy vent) "
                        "but does not want to book a visit yet. Do not call for emergencies — use escalate_to_human."),
        "execution_mode": "interactive",
        "parameters": {"type": "object",
                       "properties": {
                           "issue": _verbatim("What the issue is."),
                       },
                       "required": ["issue"]},
    },
    {
        "type": "function",
        "name": "escalate_to_human",
        "description": ("Call this IMMEDIATELY for EMERGENCIES (burst pipe, gas smell, sparking panel, no heat in freezing weather), "
                        "angry callers demanding a human, or anything you cannot resolve. "
                        "This pages a human dispatcher — do not hesitate, and do not ask for details first."),
        "execution_mode": "hold",
        "parameters": {"type": "object",
                       "properties": {
                           "reason": _verbatim("Why this needs a human."),
                       },
                       "required": ["reason"]},
    },
    {
        "type": "function",
        "name": "respond_freely",
        "description": ("Call this for small talk or off-script remarks ('are you a real person?', jokes, "
                        "'what was your name?') so you can answer briefly and steer back to helping. "
                        "Takes no action. When nothing else fits, call THIS — never stay silent."),
        "execution_mode": "interactive",
        "parameters": {"type": "object", "properties": {}},
    },
]

# Revealed only after check_availability returns real slots (progressive reveal).
BOOK_TOOL = {
    "type": "function",
    "name": "book_appointment",
    "description": ("Call this ONLY after check_availability returned open slots and the caller picked one. "
                    "Creates a booking REQUEST pending human dispatcher approval — it is not confirmed until approved. "
                    "Copy the slot_id exactly as check_availability returned it."),
    "execution_mode": "hold",
    "parameters": {"type": "object",
                   "properties": {
                       "slot_id": {"type": "string",
                                   "description": "Slot ID exactly as returned by check_availability. Copy it verbatim.",
                                   "examples": ["slot_16_morning"]},
                       "name": _verbatim("Customer's full name."),
                       "phone": _verbatim("Customer's phone number — repeat it back to confirm first."),
                       "notes": {"type": "string", "description": "Anything the tech should know, in the caller's words."},
                   },
                   "required": ["slot_id", "name", "phone"]},
}


def build_session_update(caller_context: str = "", phase: int = 0) -> dict:
    """Return the session dict for a `session.update` event."""
    from datetime import date, timedelta
    today = date.today()
    tomorrow = today + timedelta(days=1)
    tools = list(PHASE_0_TOOLS) + ([BOOK_TOOL] if phase >= 1 else [])
    prompt = SYSTEM_PROMPT.format(
        today_long=today.strftime("%A, %B %d, %Y"),
        today=today.isoformat(),
        tomorrow=tomorrow.isoformat(),
    )
    if caller_context:
        prompt += f"\n\nCALLER CONTEXT (from cross-session memory):\n{caller_context}\n"
    return {
        "system_prompt": prompt,
        "greeting": GREETING,
        "input": {
            "keyterms": KEYTERMS,
            "transcription_prompt": TRANSCRIPTION_PROMPT,
            "voice_focus": "near-field",
            "turn_detection": {"barge_in": True},
        },
        "output": {"type": "audio", "voice": "eve"},
        "tools": tools,
    }


def reveal_booking_phase() -> dict:
    """A follow-up session.update that reveals the booking tool (progressive reveal)."""
    return {"tools": list(PHASE_0_TOOLS) + [BOOK_TOOL]}
