# lablab.ai submission brief — Robin Voice Ops

Event: **AssemblyAI Voice Agent Hackathon** (Sep 1–30, 2026, online)
Submitter: Solana Minter — Purple Castle Ventures (matches lablab.ai registration)
Type: solo entry

## Title
Robin Voice Ops — the voice dispatcher for home-services businesses

## Short description (≤ 280 chars)
An AI voice dispatcher for contractors that books appointments, checks job status, and escalates emergencies — with human approval gates on every irreversible action and a hash-chained audit log. Built on the AssemblyAI Voice Agent API.

## Long description
Small home-services businesses miss a large share of calls during job hours (industry reports put it as high as ~62%), and every
missed call is $200–$500 walking to a competitor. Robin Voice Ops answers every
call, 24/7, and — unlike prompt-wrapper "AI receptionists" — it *does things*:
it looks up real availability, holds bookings for human approval, checks live
job status, answers from a knowledge base, files service requests, and escalates
emergencies to a human.

Trust is the product: nothing irreversible happens on voice alone. Bookings and
escalations enter a dispatcher approval queue; the agent waits in hold mode
until a human approves or rejects, then delivers the outcome back into the
conversation. Every tool call, approval request, and decision lands in a
SHA-256 hash-chained audit log, verifiable in the dispatcher dashboard.

Deep AssemblyAI usage: inline session.update with keyterms biasing,
transcription prompt, and voice_focus; JSON-Schema function tools with
interactive vs hold execution modes; progressive tool reveal (the booking tool
only exists after real slots are found, so the model cannot fabricate
bookings); temp-token auth; session.resume; word-level agent captions; and a
Twilio PSTN bridge so it works on a real phone line.

Measured on the live API: p50/p95 turn latency and task-completion rate are
published in the repo (evals/ + web/evals.json) — not adjectives.

## Tags
voice-agent, assemblyai, customer-support, home-services, tool-calling,
human-in-the-loop, audit-log, twilio, small-business

## Links
- Live app: *(Vercel URL — parent to fill after deploy)*
- GitHub (MIT): https://github.com/solanaminter/robin-voice-ops
- Demo video (≤3 min, narrated): *(YouTube URL — parent to fill after upload)*
- Pitch deck: *(served at /slides.html on the live demo)*

## Judging-criteria mapping
- **Application of Technology:** session.update depth (keyterms, transcription
  prompt, voice_focus, barge-in), interactive/hold execution modes, progressive
  tool reveal, temp-token auth, session.resume, Twilio bridge.
- **Business Value:** $38B SAM, $199–$499/mo SaaS + usage pricing, wedge into
  HVAC/plumbing booking.
- **Originality:** approval-gated side effects + hash-chained audit log — no
  current entry does this; not another interview coach.
- **Presentation:** live demo (no-key scripted mode included), narrated video,
  deck, published evals, MIT repo.

## Pre-submit checklist
- [ ] Vercel deploy live, URL in README + this brief
- [ ] GitHub repo public, MIT license, README links video + demo
- [ ] YouTube video public, ≤3 min, linked in README
- [ ] Deck reachable at /slides.html on the live demo
- [ ] evals.json populated from a full live eval run
- [ ] lablab.ai account registered (CAPTCHA pending user action)
