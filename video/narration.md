# Robin Voice Ops — demo video narration script

Narrator: local Kokoro `af_heart` (free; "Marcia" is a HeyGen voice and HeyGen is out of scope for this build — af_heart is the closest local female narrator).
Target: ~2:40 of narration (≤ 3:00 total video).

---

## s1_hook
Small home-services businesses run on the phone — but during job hours, there's nobody to pick up. Industry reports say contractors miss up to sixty-two percent of calls. Every missed call is two to five hundred dollars walking to a competitor. An after-hours dispatcher costs three thousand dollars a month. And today's AI receptionists? They take messages. They can't do anything.

## s2_walkthrough
Meet Robin Voice Ops — an AI voice dispatcher built on the AssemblyAI Voice Agent API. Watch a real call. The caller says: "I need a plumber tomorrow morning for a leaking water heater." Robin checks real availability — then comes the key moment. It does not just book. The booking fires in hold mode and pends in the dispatcher's approval queue. The human approves with one click — and Robin speaks the confirmation straight back into the call. If it's rejected, Robin offers the next slot. No hallucinated bookings, ever. And every tool call, approval, and decision lands in a hash-chained audit log you can verify in the dashboard.

## s3_architecture
Under the hood: the caller's audio streams to the AssemblyAI Voice Agent API over a single WebSocket. Tool calls hit our backend — instant lookups run immediately, while bookings and escalations hold for human approval. Everything persists in Postgres or SQLite: jobs, slots, the knowledge base, and cross-session caller memory — so repeat callers are recognized by name. And a SHA-256 hash-chained audit log records every action.

## s4_integration
This is not a prompt wrapper. The session is configured inline: keyterms biasing, a transcription prompt, near-field voice focus, and barge-in turn detection. Tools are declared as JSON Schema with interactive versus hold execution modes. The booking tool is only revealed after real slots come back — so the model structurally cannot invent a booking. Auth uses temporary tokens, so the API key never reaches the browser. Session resume reconnects dropped calls, word-level captions keep the UI live, and a Twilio bridge puts this on a real phone line.

## s5_evidence
Measured, not adjectives. Against the live API, Robin completes six out of six scripted voice scenarios — booking, job status, FAQs, emergency escalation, and off-script handling — with a median decision latency of one point eight seconds. Twenty-five out of twenty-five offline tests pass: approval gates, audit-log tamper detection, caller memory, and more.

## s6_closing
Robin Voice Ops — the voice dispatcher that does things. Try the live demo, or read the code. Links below.
