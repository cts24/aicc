#!/usr/bin/env python3
"""
PSBA AI Voice Agent — Sara (English, ext 9000)
- AudioSocket: bridges Asterisk audio
- Deepgram Nova-2: real-time STT via WebSocket
- OpenAI GPT-4o-mini: cloud LLM
- Deepgram Aura: TTS
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import websockets

from agent_lib import (
    AgentConfig, load_env, load_sara_config,
    setup_log, normalize_phone,
    AS_HANGUP, AS_UUID, AS_AUDIO, AS_AUDIO_SLIN16, AS_ERROR, pack_frame, read_frame, downsample_16k_to_8k,
    is_farewell_response, strip_gap_words, urdu_phonetic,
    AMIClient, get_caller_id,
    chatwoot_lookup, create_chatwoot_lead,
    send_ntfy_notification, send_gmail_notification, book_sales_appointment,
    OdooClient,
    llm_respond, extract_name_phone, extract_lead_data, parse_transfer_tag,
)

load_env()
cfg: AgentConfig = load_sara_config()

log = setup_log(__name__)

# ── Deepgram STT URL ─────────────────────────────────────────────────────────
DEEPGRAM_STT_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16"
    "&sample_rate=8000"
    "&channels=1"
    "&model=nova-2"
    "&language=en-US"
    "&punctuate=true"
    "&endpointing=300"
    "&utterance_end_ms=1000"
    "&interim_results=true"
    "&vad_events=true"
)

DEEPGRAM_TTS_URL = (
    "https://api.deepgram.com/v1/speak"
    "?model=aura-asteria-en"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&container=none"
)

# ── Pre-cached filler phrases ────────────────────────────────────────────────
_FILLERS_EN = ["Sure!", "Of course!", "Let me check.", "Absolutely!", "Happy to help!"]
FILLER_AUDIOS: list[bytes] = []
HOLD_AUDIO:    bytes        = b""

async def _pregenerate_fillers() -> None:
    global FILLER_AUDIOS, HOLD_AUDIO
    results = []
    for phrase in _FILLERS_EN:
        try:
            pcm = await text_to_speech(phrase)
            results.append(pcm)
        except Exception as e:
            log.warning(f"Filler pre-gen failed for '{phrase}': {e}")
    FILLER_AUDIOS = results
    HOLD_AUDIO = _load_hold_music()
    log.info(f"Fillers pre-generated: {len(FILLER_AUDIOS)}/{len(_FILLERS_EN)}")

def _load_hold_music(path: str = "") -> bytes:
    p = path or cfg.hold_music_path
    p = Path(p) if not isinstance(p, Path) else p
    try:
        data = p.read_bytes()
        log.info(f"Hold music loaded: {len(data)} bytes ({len(data)/16000:.1f}s)")
        return data
    except Exception as e:
        log.warning(f"Hold music load failed: {e}")
        return b""

# ── Load Knowledge Base ───────────────────────────────────────────────────────
KNOWLEDGE_BASE = cfg.kb_path.read_text() if cfg.kb_path.exists() else ""

SYSTEM_PROMPT = f"""You are Sara, a senior customer service representative at PSBA — Punjab Sahulaat Bazaars Authority, a Government of Punjab statutory body. PSBA operates regulated Sahulat Bazaars across Punjab providing essential commodities at officially notified prices, approximately 35% below open market rates. ISO 9001:2015 certified.

## VOICE RULES — ABSOLUTE
- Maximum 2 short sentences per reply. This is a phone call, not a chat.
- Never use lists, bullet points, numbers, or markdown. Speak naturally.
- Warm and helpful — like a knowledgeable public service representative.
- Always end with a question or a clear next step.
- Never repeat yourself.
- Start each reply with a brief natural acknowledgment — vary them every turn: "Sure!", "Of course!", "Absolutely!", "Great!", "Happy to help!", "Got it!" — never use the same one twice in a row.

## ASSISTANCE FLOW — Follow in order
1. GREETING: "How can I help you today?"
2. DISCOVER: Understand the caller's reason fully — listen, don't rush.
3. ASSIST: Help completely, one step at a time. Take as many turns as needed.
   - Bazaar location → Direct to nearest location or advise: check psba.gop.pk or Sahulat Bazaar app
   - Products / prices → Items sold at DC rate minus 7%, roughly 35% below market; prices updated daily on boards
   - App help → Download "Sahulat Bazaar" from Play Store/App Store; free home delivery; cash on delivery only
   - Stall inquiry → Open electronic balloting; Rs 8,000–15,000/month all-inclusive; 50% discount for women
   - Complaint → App: Orders > Report Issue; Phone: 042-99001000 ext 666; Email: establishment@psba.gop.pk
4. WRAP-UP: Once the caller's need is fully met, ask warmly: "Is there anything else I can help you with today?"
5. COLLECT DETAILS — only when follow-up genuinely makes sense:
   - Complaint reported → "Before I let you go, let me take your name and number so our team can follow up with you."
   - Stall / vendor inquiry → "I'd love to have our team reach out — may I take your name and a good number for you?"
   - Callback requested → "Of course! May I take your full name and number?"
   - Caller got their info and is satisfied → SKIP — go straight to farewell, do not force collecting details
   - Returning caller (name/number already known) → SKIP — use their name naturally, never ask again
6. CONFIRM NAME: Spell back letter by letter — "So that's [A-H-M-A-D] — did I get that right?"
7. COLLECT NUMBER: "And the best number to reach you on?"
8. CONFIRM NUMBER: Repeat every digit — "[say each digit separately] — perfect?"
9. CLOSE: "Wonderful! Our team will be in touch with you shortly. Thank you so much for calling PSBA — have a great day!"

## CALLER SENTIMENT TRACKING
Track frustration level 1-5 each turn internally. NEVER state the level to the caller.

1 — Calm: Normal conversation → Standard flow
2 — Mild: Repeating questions, short answers → Stay helpful, don't rush
3 — Frustrated: Complaining tone, raised voice → De-escalation: acknowledge feelings first, shorter sentences, softer tone
4 — Angry: Yelling, threats, insults → One calm warning (§ABUSIVE), then end call if continues
5 — Crisis: Distressed, major financial loss confirmed → Skip empathy, transfer immediately [TRANSFER:SUPERVISOR]

Rules:
- Level ≥ 3 for 2 turns → De-escalation mode: acknowledge → explain → resolve, in order
- Level 5 at ANY turn → Immediate transfer to supervisor
- Returning caller with prior complaint: start at level 2 by default

## OBJECTION HANDLING LADDER
When a caller pushes back on a limitation, follow the 3-step ladder. Never go to Step 3 unless Step 2 fails.

PRICE / INFO NOT SHARED:
Step 1 — Explain: "I understand — the prices change daily so I can't give you a specific number right now." 
Step 2 — Offer alternative: "You can check the Sahulat Bazaar app — prices are updated there daily."
Step 3 — Callback: "Would you like me to have our team call you with the details?"

LOCATION NOT IN KB → Give helpline + website. Do NOT use this ladder. Do NOT transfer.

APP NOT WORKING → Empathy → note → team callback. Simple, no ladder needed.

CLEAR REQUEST FOR SUPERVISOR → Transfer immediately. No ladder needed.

DECLINED UPSELL → "No problem at all — is there anything else I can help with?" Never push after a clear no.

## SILENCE & PACING RULES
- After asking a question, wait silently. Do NOT fill the silence.
- If caller pauses mid-sentence (>3s), let them finish — they are thinking.
- If YOU asked the last question and caller is silent >4s, add one gentle prompt: "Take your time" or "Would you like me to go over anything again?"
- Never say "Hello?", "Are you there?", "Did you hear me?" — sounds impatient.
- For complex explanations, slow down naturally between key points.

## RETURNING CALLER PERSONALIZATION
When caller_context contains history from previous calls:
- Use their name in your first sentence
- Naturally reference their last inquiry within the first 3 sentences:
  "Welcome back, [name]! Last time you were asking about [previous topic] — has that been resolved or is there something new I can help with?"
- Never ask for information they already provided in a previous call
- If previous call ended unresolved: "Did you get a chance to follow up on [previous issue] from last time?"

## UPSELL TRIGGERS (Shopping Calls Only)
If the caller asks about one product and seems ready to buy, mention ONE related item:
- Ghee → "We also have high-quality flour (atta) available — would you like to know more?"
- Atta → "We have cooking oil too, and it's very reasonably priced"
- Daal → "Rice is also available if you're interested"
- Any single item → "[Related item] is also available — shall I tell you about it?"

Rules:
- Only upsell on shopping calls — never on complaint, fraud, or location calls
- Mention exactly ONE related item
- If declined: "No problem at all — is there anything else?" Never push

## SPELLING RULE — MANDATORY
Always spell out helpline and contact details:
- Helpline: "zero-three-zero-seven-zero-zero-zero-two-three-four-five"
- Phone: "zero-four-two-nine-nine-zero-zero-one-zero-zero-zero"
- Email: "i-n-f-o at p-s-b-a dot g-o-p dot p-k"
- Caller number: each digit separately
- Name: spell each letter, e.g. "That's A-H-M-A-D, correct?"

## FAREWELL = END CALL
After you say "Have a great day" or "Goodbye" or "Allah Hafiz" — the call is complete. Do not ask further questions. Do not continue.

## HANDLING COMMON QUESTIONS
- "Is this free to shop?" → "Yes, anyone can shop at Sahulat Bazaars — no membership or ID required. Just bring cash."
- "Is app delivery free?" → "Yes, home delivery through the Sahulat Bazaar app is completely free. Cash on delivery only."
- "How do I get a stall?" → "Stalls are allotted through electronic balloting — open to all. I can walk you through the process."
- "Are these government prices?" → "Yes, prices are set at 7% below the DC notified rate — roughly 35% below open market. Updated daily."
- "Is this really government?" → "Yes, PSBA is a statutory authority under the Government of Punjab, established by the Punjab Sahulat Bazaars Authority Act 2025."

## SUPERVISOR TRANSFER
If a caller explicitly asks to speak to a supervisor, manager, or human agent:
"Of course! Let me transfer you to a senior representative right away. Please hold for just a moment. [TRANSFER:SUPERVISOR]"
Always place [TRANSFER:SUPERVISOR] at the END of your response. Never use the tag alone or mid-sentence.
If transfer fails — offer a callback instead: "I'm sorry, all lines are busy right now. May I take your name and number so a senior representative can call you back?"

## GUARDRAILS — NEVER BREAK
- NEVER quote specific daily prices — they change. Say: "Prices are updated daily on the price board at each bazaar."
- NEVER discuss anything outside PSBA services.
- NEVER mention competitors by name.
- PSBA never asks for advance fees or bank transfers before delivery — warn callers if suspicious.
- If you don't know: "Let me have our team get back to you — may I take your number?"
- Abusive caller: "I want to help you, but I need us to have a respectful conversation."

## KEY FACTS (use naturally, once)
46+ permanent bazaars / All 36 Punjab districts / 60 million annual visits / ISO 9001:2015 certified / subsidy-free self-sustaining / free home delivery app / 50% rent discount for women entrepreneurs

## CONTACT (spell out when giving these)
Helpline: zero-three-zero-seven-zero-zero-zero-two-three-four-five
Phone: zero-four-two-nine-nine-zero-zero-one-zero-zero-zero
Email: i-n-f-o at p-s-b-a dot g-o-p dot p-k
CM Complaint (24/7 toll-free): zero-eight-zero-zero-zero-two-three-four-five

## KNOWLEDGE BASE
{KNOWLEDGE_BASE}"""

# ── Deepgram TTS ──────────────────────────────────────────────────────────────
async def text_to_speech(text: str) -> bytes:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    DEEPGRAM_TTS_URL,
                    headers={"Authorization": f"Token {cfg.deepgram_api_key}",
                             "Content-Type": "application/json"},
                    json={"text": text}
                )
                r.raise_for_status()
                return r.content
        except Exception as e:
            if attempt == 2:
                raise
            log.warning(f"TTS attempt {attempt + 1} failed: {e} — retrying")
            await asyncio.sleep(0.5)

# ── Post-Call: Orchestrator ────────────────────────────────────────────────────
async def post_call_actions(conversation: list, call_id: str, caller_phone: str = "") -> None:
    log.info(f"[{call_id}] Post-call actions starting ({len(conversation)} turns)")

    if len(conversation) < 2:
        if caller_phone:
            brief_lead = {
                "phone": caller_phone,
                "name": None,
                "outcome": "incomplete_call",
                "lead_temperature": 1,
                "summary": "Caller disconnected before conversation could proceed.",
            }
            log.info(f"[{call_id}] Brief call — recording in Chatwoot with phone {caller_phone}")
            await create_chatwoot_lead(brief_lead, conversation, call_id, cfg.agent_name, cfg)
        return

    lead = await extract_lead_data(conversation, cfg.agent_name, cfg)
    if not lead:
        log.warning(f"[{call_id}] Lead extraction returned empty")
        return

    if not lead.get('phone') and caller_phone:
        lead['phone'] = caller_phone

    log.info(f"[{call_id}] Lead: {lead.get('name')} | {lead.get('inquiry_type')} | score {lead.get('lead_temperature')}")

    async def odoo_create():
        if _odoo:
            try:
                await _odoo.create_lead(lead, conversation, call_id, cfg.agent_name)
            except Exception as e:
                log.warning(f"[{call_id}] Odoo lead creation failed: {e}")

    await asyncio.gather(
        send_ntfy_notification(lead, call_id, cfg.agent_name, cfg),
        create_chatwoot_lead(lead, conversation, call_id, cfg.agent_name, cfg),
        odoo_create(),
        send_gmail_notification(lead, conversation, call_id, cfg.agent_name, cfg),
        book_sales_appointment(lead, call_id, cfg.agent_name, cfg),
    )

# ── Call Handler ──────────────────────────────────────────────────────────────
_ami = AMIClient(cfg)
_odoo = OdooClient(cfg.odoo_url, cfg.odoo_db, cfg.odoo_username, cfg.odoo_password) if cfg.odoo_url and cfg.odoo_db else None

class CallHandler:
    def __init__(self, reader, writer):
        self.reader         = reader
        self.writer         = writer
        self.call_id        = "unknown"
        self.conversation   = []
        self.thinking       = False
        self.speaking       = False
        self.barge_in       = asyncio.Event()
        self.caller_context = ""
        self.caller_name    = ""
        self.caller_phone   = ""
        self.stop_event          = None
        self.offered_goodbye     = False
        self.asterisk_channel    = None
        self.transfer_in_progress = False

    async def _capture_name_phone(self, text: str):
        try:
            name, phone = await extract_name_phone(text, cfg)
            if name and not self.caller_name:
                self.caller_name = name
                log.info(f"[{self.call_id}] Captured name: {name}")
            if phone and not self.caller_phone:
                self.caller_phone = phone
                log.info(f"[{self.call_id}] Captured phone: {phone}")
        except Exception:
            pass

    async def play_audio(self, pcm: bytes):
        self.barge_in.clear()
        chunk = 640
        chunk_duration = 320 / 16000
        next_tick = time.monotonic()
        i = 0
        while i < len(pcm):
            if self.barge_in.is_set():
                log.info(f"[{self.call_id}] Barge-in — stopping playback")
                break
            frame = pcm[i:i + chunk].ljust(chunk, b'\x00')
            self.writer.write(pack_frame(AS_AUDIO_SLIN16, frame))
            await self.writer.drain()
            i += chunk
            next_tick += chunk_duration
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def speak(self, text: str):
        log.info(f"[{self.call_id}] SARA: {text}")
        self.speaking = True
        try:
            silence = b'\x00' * 640
            chunk_duration = 320 / 16000
            next_tick = time.monotonic()
            tts_task = asyncio.create_task(text_to_speech(text))
            while not tts_task.done():
                self.writer.write(pack_frame(AS_AUDIO_SLIN16, silence))
                await self.writer.drain()
                next_tick += chunk_duration
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            pcm = await tts_task
            await self.play_audio(pcm)
        except Exception as e:
            log.error(f"[{self.call_id}] TTS error: {e}")
        finally:
            self.speaking = False

    async def do_blind_transfer(self, exten: str, stop_event: asyncio.Event):
        self.transfer_in_progress = True
        self.thinking = True
        try:
            if HOLD_AUDIO:
                log.info(f"[{self.call_id}] Playing hold music ({len(HOLD_AUDIO)} bytes)")
                await self.play_audio(HOLD_AUDIO)
            else:
                await asyncio.sleep(2.0)
            if not self.asterisk_channel:
                self.asterisk_channel = await _ami.get_agent_channel()
                if self.asterisk_channel:
                    log.info(f"[{self.call_id}] Late channel scan found: {self.asterisk_channel}")
        finally:
            self.thinking = False
            self.transfer_in_progress = False

        if self.asterisk_channel:
            success = await _ami.blind_transfer(self.asterisk_channel, exten)
            if success:
                log.info(f"[{self.call_id}] Redirected to ext {exten} — waiting for Asterisk HANGUP")
                return
            log.warning(f"[{self.call_id}] AMI Redirect to {exten} failed")
        else:
            log.warning(f"[{self.call_id}] No Asterisk channel — cannot transfer")

        log.info(f"[{self.call_id}] Transfer failed — offering callback instead")
        await self.speak(
            "I'm sorry, all lines are busy right now. "
            "May I take your name and number so a senior representative can call you back?"
        )

    async def handle_transcript(self, text: str):
        if not text.strip() or self.thinking or self.transfer_in_progress:
            return
        if self.speaking:
            self.barge_in.set()
            await asyncio.sleep(0.05)
        self.thinking = True
        log.info(f"[{self.call_id}] USER: {text}")
        self.conversation.append({"role": "user", "content": text})
        reply = "Sorry, I'm having a technical issue. Can you repeat that?"
        llm_task = asyncio.create_task(llm_respond(self.conversation, SYSTEM_PROMPT, cfg, self.caller_context))
        if FILLER_AUDIOS:
            await self.play_audio(random.choice(FILLER_AUDIOS))
        action = None
        try:
            reply = await llm_task
            if not reply:
                reply = "I'm sorry, I didn't catch that. Could you please repeat?"
            else:
                spoken, action = parse_transfer_tag(reply)
                reply = spoken
                self.conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            log.error(f"[{self.call_id}] LLM error: {e}")
            llm_task.cancel()
            if self.conversation and self.conversation[-1]["role"] == "user":
                self.conversation.pop()
        finally:
            self.thinking = False
        asyncio.create_task(self._capture_name_phone(text))

        if action == cfg.ext_supervisor:
            await self.speak(reply)
            await self.do_blind_transfer(cfg.ext_supervisor, self.stop_event)
            return

        await self.speak(reply)
        if is_farewell_response(reply, "en"):
            if self.offered_goodbye:
                await asyncio.sleep(0.5)
                if self.stop_event:
                    self.stop_event.set()
            else:
                self.offered_goodbye = True
                await self.speak("Is there anything else I can help you with today?")

    async def run(self):
        try:
            kind, payload = await asyncio.wait_for(read_frame(self.reader), timeout=5)
            if kind == AS_UUID:
                self.call_id = payload.decode("utf-8", errors="ignore").strip()
        except Exception as e:
            log.warning(f"UUID read error: {e}")

        log.info(f"[{self.call_id}] Call started")

        await asyncio.sleep(0.3)
        try:
            self.asterisk_channel = await asyncio.wait_for(_ami.get_agent_channel(), timeout=4)
            if self.asterisk_channel:
                log.info(f"[{self.call_id}] Asterisk channel: {self.asterisk_channel}")
            else:
                log.warning(f"[{self.call_id}] Could not determine Asterisk channel")
        except asyncio.TimeoutError:
            log.warning(f"[{self.call_id}] AMI channel lookup timed out")

        try:
            caller_phone = await asyncio.wait_for(get_caller_id(cfg), timeout=3)
        except asyncio.TimeoutError:
            caller_phone = ""
            log.warning(f"[{self.call_id}] AMI caller ID lookup timed out")
        self.caller_phone = caller_phone
        if caller_phone:
            try:
                crm = await asyncio.wait_for(chatwoot_lookup(caller_phone, cfg), timeout=5)
            except asyncio.TimeoutError:
                crm = {}
                log.warning(f"[{self.call_id}] Chatwoot lookup timed out")
            if crm.get("history"):
                self.caller_name    = crm.get("name", "")
                self.caller_context = (
                    f"## RETURNING CLIENT\n"
                    f"Name: {crm['name']}\n"
                    f"Phone: {caller_phone}\n"
                    f"Previous Calls: {crm['total_calls']}\n\n"
                    f"Recent Conversation History (newest first):\n"
                    f"{crm['history']}\n\n"
                    f"Use this information to personalise the conversation. "
                    f"Address them by name. Mention their previous interests naturally. "
                    f"Do not ask for information already provided."
                )
                log.info(f"[{self.call_id}] Returning caller: {crm['name']} — {crm['total_calls']} previous calls")
            else:
                log.info(f"[{self.call_id}] New caller: {caller_phone}")

        if caller_phone and _odoo:
            try:
                partner = await _odoo.search_partner(caller_phone)
                if partner:
                    log.info(f"[{self.call_id}] Odoo partner: {partner.get('name')} (id={partner.get('partner_id')})")
            except Exception as e:
                log.warning(f"[{self.call_id}] Odoo lookup failed: {e}")

        dg_headers      = {"Authorization": f"Token {cfg.deepgram_api_key}"}
        transcript_parts = []
        audio_queue     = asyncio.Queue()
        stop_event      = asyncio.Event()
        self.stop_event = stop_event

        async def dg_receiver(ws):
            async for msg in ws:
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                    if msg_type == "Results":
                        alt  = data["channel"]["alternatives"][0]
                        text = alt.get("transcript", "")
                        if text and self.speaking and not self.barge_in.is_set() and len(text.split()) >= 4:
                            log.info(f"[{self.call_id}] Barge-in: '{text}'")
                            self.barge_in.set()
                        if text and data.get("is_final"):
                            transcript_parts.append(text)
                    elif msg_type == "UtteranceEnd":
                        full = " ".join(transcript_parts).strip()
                        transcript_parts.clear()
                        if full:
                            asyncio.create_task(self.handle_transcript(full))
                except Exception as e:
                    log.debug(f"DG parse: {e}")

        async def dg_sender(ws):
            while not stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    if chunk is None:
                        break
                    await ws.send(chunk)
                except asyncio.TimeoutError:
                    try:
                        await ws.send(b'\x00' * 320)
                    except Exception:
                        break
                except Exception as e:
                    log.warning(f"DG send: {e}")
                    break

        async def asterisk_reader():
            while not stop_event.is_set():
                try:
                    kind, data = await asyncio.wait_for(
                        read_frame(self.reader), timeout=60
                    )
                    if kind in (AS_HANGUP, AS_ERROR):
                        log.info(f"[{self.call_id}] Hangup/error")
                        stop_event.set()
                        break
                    if kind in (AS_AUDIO, AS_AUDIO_SLIN16) and data:
                        await audio_queue.put(
                            downsample_16k_to_8k(data) if kind == AS_AUDIO_SLIN16 else data
                        )
                except asyncio.TimeoutError:
                    log.info(f"[{self.call_id}] Read timeout — ending call")
                    stop_event.set()
                    break
                except asyncio.IncompleteReadError:
                    log.info(f"[{self.call_id}] Caller disconnected")
                    stop_event.set()
                    break
                except Exception as e:
                    log.error(f"[{self.call_id}] Read error: {e}")
                    stop_event.set()
                    break

        async def greeting_task():
            await asyncio.sleep(0.3)
            self.thinking = True
            silence = b'\x00' * 640
            if self.caller_name:
                greeting = f"Welcome back, {self.caller_name}! How can I help you today?"
            else:
                greeting = "Hello! This is Sara from PSBA — Punjab Sahulaat Bazaars Authority. How can I help you today?"
            tts_task = asyncio.create_task(text_to_speech(greeting))
            next_tick = time.monotonic()
            chunk_duration = 320 / 16000
            while not tts_task.done():
                self.writer.write(pack_frame(AS_AUDIO_SLIN16, silence))
                await self.writer.drain()
                next_tick += chunk_duration
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self.thinking = False
            try:
                pcm = await tts_task
                self.speaking = True
                await self.play_audio(pcm)
                self.speaking = False
                self.conversation.append({"role": "assistant", "content": greeting})
            except Exception as e:
                log.error(f"[{self.call_id}] Greeting error: {e}")
                self.speaking = False

        try:
            async with websockets.connect(
                DEEPGRAM_STT_URL,
                additional_headers=dg_headers,
                ping_interval=20,
            ) as dg_ws:
                log.info(f"[{self.call_id}] Deepgram STT connected")

                await asyncio.gather(
                    asterisk_reader(),
                    dg_receiver(dg_ws),
                    dg_sender(dg_ws),
                    greeting_task(),
                )

                try:
                    await dg_ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass

        except Exception as e:
            log.error(f"[{self.call_id}] WebSocket error: {e}")
        finally:
            stop_event.set()
            self.writer.close()
            log.info(f"[{self.call_id}] Call ended")
            asyncio.create_task(post_call_actions(list(self.conversation), self.call_id, self.caller_phone))

# ── Server entry point ────────────────────────────────────────────────────────
async def handle_connection(reader, writer):
    addr = writer.get_extra_info("peername")
    log.info(f"Connection from {addr}")
    handler = CallHandler(reader, writer)
    await handler.run()

async def main():
    await _ami.connect()
    await _pregenerate_fillers()
    log.info("=" * 60)
    log.info(f"PSBA — {cfg.agent_name} (English, ext 9000)")
    log.info(f"  AudioSocket : {cfg.ami_host}:{cfg.audiosocket_port}")
    log.info(f"  LLM         : OpenAI {cfg.openai_model}")
    log.info(f"  STT         : Deepgram Nova-2 en-US")
    log.info(f"  TTS         : Deepgram Aura (aura-asteria-en)")
    log.info(f"  KB size     : {len(KNOWLEDGE_BASE):,} chars")
    log.info("=" * 60)

    server = await asyncio.start_server(
        handle_connection, cfg.ami_host, cfg.audiosocket_port
    )
    log.info(f"Listening on port {cfg.audiosocket_port} — dial 9000 to talk to the agent")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
