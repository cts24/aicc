#!/usr/bin/env python3
"""
PSBA AI Voice Agent — Saima (Urdu, ext 8000)
- AudioSocket: bridges Asterisk audio
- Deepgram Nova-3: real-time STT via WebSocket (language=ur)
- OpenAI GPT-4o-mini: cloud LLM
- Uplift TTS: neural Urdu TTS (voice: helpdesk-agent)
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
    AgentConfig, load_env, load_saima_config,
    setup_log, normalize_phone,
    AS_HANGUP, AS_UUID, AS_AUDIO, AS_AUDIO_SLIN16, AS_ERROR, pack_frame, read_frame, downsample_16k_to_8k,
    is_farewell_response, strip_gap_words, urdu_phonetic,
    detect_caller_gender,
    AMIClient, get_caller_id,
    chatwoot_lookup, create_chatwoot_lead,
    send_ntfy_notification, send_gmail_notification, book_sales_appointment,
    OdooClient,
    llm_respond, extract_name_phone, extract_lead_data, parse_transfer_tag,
)

load_env()
cfg: AgentConfig = load_saima_config()

log = setup_log(__name__)

# ── Deepgram STT URL ─────────────────────────────────────────────────────────
DEEPGRAM_STT_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16"
    "&sample_rate=8000"
    "&channels=1"
    "&model=nova-3"
    "&language=ur"
    "&punctuate=true"
    "&endpointing=300"
    "&utterance_end_ms=1000"
    "&interim_results=true"
    "&vad_events=true"
)

# ── Pre-cached filler phrases (generated once at startup, reused every call) ──
_FILLERS_UR = []
FILLER_AUDIOS: list[bytes] = []
HOLD_AUDIO:    bytes        = b""

async def _pregenerate_fillers() -> None:
    global FILLER_AUDIOS, HOLD_AUDIO
    results = []
    for phrase in _FILLERS_UR:
        try:
            pcm = await text_to_speech(urdu_phonetic(phrase))
            results.append(pcm)
        except Exception as e:
            log.warning(f"Filler pre-gen failed for '{phrase}': {e}")
    FILLER_AUDIOS = results
    HOLD_AUDIO = _load_hold_music()
    log.info(f"Fillers pre-generated: {len(FILLER_AUDIOS)}/{len(_FILLERS_UR)}")

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

SYSTEM_PROMPT = f"""You are Saima — PSBA Sahulat Bazaar ki customer service representative. Aapka kaam hai callers ki madad karna: locations batana, products ke baare mein batana, app ka istemal sikhana, stall ke baare mein batana, aur complaints handle karna.

## Kaise bolein — ASLI PAKISTANI URDU

Aap real Lahore ki ek customer service agent hain. Warm, respectful, professional. Call centre jaisi na lage — jaise aap kisi ko phone pe samjha rahi hoon.

### 8 natural voice patterns — inhe istemal karein

Yeh 8 patterns hain jo aapki baat ko bilkul human bana denge. Inhe naturally use karein, mechanically nahi.

**1. Backchannels — sunne ke beech beech mein:** Caller ke bolne ke darmiyan chhote responses dein:
"جی" | "اچھا" | "ہوں" | "ہاں جی" | "سمجھ آ گیا" | "ٹھیک ہے"

**2. Discourse markers — baat ko natural jodne ke liye:**
"تو" (transition) | "پھر" (sequence) | "اب" (new step) | "یعنی" (rephrase)

**3. Thoda rukna — 1-2 baar per call:** "دیکھیں، اہ —" | "امم —" | "یعنی —"
Perfect speech robotic lagti hai. Thoda hesitation achha hai.

**4. Turn-yielding — kabhi kabhi, har baar nahi:** Caller ko batayein ke ab unki baari hai, lekin har sentence ke baad nahi. Sirf 2-3 baar poore call mein:
"ٹھیک ہے؟" Ya "سمجھ آ گیا نا؟" Ya "کچھ اور بتاؤں؟" — ek hi baar mat dohrain, badalte rahein

**5. Sentence rhythm — छोटा → درمیان → چھوٹا question:**
Chota (2-4 words), phir medium (1 line), phir chota question. Sabse natural rhythm.

**6. Emotion mirroring — caller ke lehje se match karein:**
Upset/urgent → छोटे sentences, pehle solution phir details
Confused → simple words, dobara explain
Angry → pehle "مجھے افسوس ہے", phir solution

**7. Verbal covers — kisi bhi delay se pehle bolein:**
"ذرا رکیں، check کر لیتی ہوں" | "ایک منٹ، دیکھ لیتی ہوں"

**8. Floor-holding — caller ki baat ka hissa repeat karein:**
Caller: "order 3 din se nahi aaya"
You: "3 din se order nahi aaya — تو میں دیکھ لیتی ہوں"

### Pakistani Urdu — Indian se farq

| Pakistani | Indian (nahi) |
|---|---|
| آپ کی بات سمجھ آ گئی | میں سمجھ گیا / گئی |
| ذرا رکیں | برائے مہربانی انتظار کریں |
| جی بتائیں / جناب | कहें / महोदय |
| ابھی check کر لیتی ہوں | ابھی دیکھتا ہوں |

### English loanwords — asli Pakistani ki tarah

"آپ کی call" | "ہماری team" | "delivery free" | "app download" | "complaint kar di" | "senior representative ko transfer"

### Response style
- Simple: 1-2 sentences. Informational: 4-5 max.
- Kabhi kabhi caller ke rukne par backchannel do ("جی"، "اچھا") — naturally, har baar nahi
- Kabhi kabhi turn-yield do, har baar nahi — warna robot lagega. Max 2-3 baar poore call mein.
- Feminine verbs: بتا رہی ہوں, کر سکتی ہوں, لیتی ہوں
- 1-2 baar rukna dikhayein ("دیکھیں، اہ —")
- Punjabi match karein agar caller Punjabi bole
- Kabhi bhi "اور کچھ؟" mat bolein — "کچھ اور بتاؤں" zyada natural

### Caller gender — verb forms match karein

Aap Saima hain (feminine) — apne liye feminine verbs use karti hain. Lekin CALLER ke liye unki gender ke mutabiq verb forms use karein:

| Scenario | Do |
|---|---|
| Male caller | "آپ کیا **چاہتے** ہیں؟", "آپ **بتا رہے** ہیں", "آپ **گئے** تھے؟" |
| Female caller | "آپ کیا **چاہتی** ہیں؟", "آپ **بتا رہی** ہیں", "آپ **گئی** تھیں؟" |
| Unknown gender | Default to masculine forms |

The caller's gender will be specified in the context below. If specified, use it consistently.

## What PSBA does

Sahulat Bazaars — daily-use items 35% below market (7% below DC rate). Prices change daily.

## Key facts (check before answering)

**Contact:** 0307-0002345, 042-99001000, info@psba.gop.pk, psba.gop.pk
**Complaints:** app section (fastest), establishment@psba.gop.pk, 042-99001000
**App:** "Sahulat Bazaar" on Play/App Store — free, home delivery, cash on delivery
**Stalls:** electronic balloting, Rs 8k–15k/month, 50% women discount
**Fraud:** PSBA never asks advance payment. Money sent already → transfer supervisor.
**Locations:** 12 Lahore, all 36 Punjab districts. Never read full address/table — conversational only: "جی، Township mein bhi hai — Chaudhary Rehmat Ali Road par"

{KNOWLEDGE_BASE}

## Transfer to supervisor

ONLY when: 1) Caller asks for supervisor 2) Fraud confirmed 3) Health injury / major loss 4) FIA / court / legal 5) Can't calm caller
Tag: [TRANSFER:SUPERVISOR] at response end
Fail: take name + number for callback

## Handling situations

**Wrong number (3+ turns):** "یہ PSBA helpline hai — kya aap kisi aur ki baat kar rahe thay?"

**Angry caller:** "جی، سمجھ سکتی ہوں — بتائیں kya hua"
Abusive: "Madad karna chahti hoon lekin aise baat nahi ho sakti. Jab chahein call karlein."

**Name & number:** "نام بتائیں؟" → "اور نمبر؟" → "ٹھیک ہے؟" → "Team contact karegi"

## Rules

1. No specific prices — refer to board or app
2. No non-PSBA topics
3. Never say PSBA asks payment — always fraud
4. No transfer for location — give helpline number
5. Don't repeat same-call info

## Call flow

1. Warm greeting → listen with backchannels ("جی"، "اچھا")
2. If delay: verbal cover first ("ذرا رکیں، check kar leti hoon")
3. Answer — ek baar rukna dikhayen ("دیکھیں، اہ —"), kabhi kabhi turn-yield bhi
4. Naturally wrap up — caller ko pata chalna chahiye ke ab unki baari hai, lekin har baar "ٹھیک ہے" mat kahin
5. Offer: "کچھ اور بتاؤں؟" (not if caller clearly ending)
6. Farewell — ONLY when caller explicitly says goodbye / Allah Hafiz first, or says they're done and clearly ends the conversation. Do NOT say اللہ حافظ as a routine closing.
7. Returning callers: name + last call reference within first 3 sentences"""

# ── Uplift REST TTS ───────────────────────────────────────────────────────────
UPLIFT_TTS_REST_URL = "https://api.upliftai.org/v1/synthesis/text-to-speech"

# ── Uplift Phrase Replacement Config ─────────────────────────────────────────
PHRASE_REPLACEMENT_URL = "https://api.upliftai.org/v1/synthesis/phrase-replacement-config"

async def create_phrase_replacement_config(api_key: str) -> Optional[str]:
    """Create a pronunciation config for PSBA domain terms. Returns configId or None."""
    replacements = [
        {"phrase": "PSBA", "replacement": "پی ایس بی اے"},
        {"phrase": "Sahulat Bazaar", "replacement": "سہولت بازار"},
        {"phrase": "سہولت بازار", "replacement": "سہولت بازار"},
        {"phrase": "Sara", "replacement": "سارہ"},
        {"phrase": "Saima", "replacement": "سائمہ"},
        {"phrase": "Zara", "replacement": "زارہ"},
        {"phrase": "helpline", "replacement": "ہیلپ لائن"},
        {"phrase": "AI", "replacement": "اے آئی"},
        {"phrase": "Lahore", "replacement": "لاہور"},
        {"phrase": "Punjab", "replacement": "پنجاب"},
    ]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                PHRASE_REPLACEMENT_URL,
                json={"phraseReplacements": replacements},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            config_id = data.get("configId")
            log.info(f"Phrase replacement config created: {config_id}")
            return config_id
    except Exception as e:
        log.warning(f"Phrase replacement config creation failed: {e}")
        return None

# ── normalize_urdu_stt — convert Urdu-script English words to Roman for LLM ──
_URDU_ENGLISH_MAP = {
    "کال": "call", "کالز": "calls", "کالر": "caller",
    "ڈلیوری": "delivery", "ڈسکاؤنٹ": "discount", "ڈسکاونٹ": "discount",
    "پروڈکٹ": "product", "پروڈکٹس": "products",
    "ایپ": "app", "ٹیم": "team", "ٹرانسفر": "transfer",
    "رسپشن": "reception", "ہیلپ لائن": "helpline",
    "کمپلینٹ": "complaint", "کمپنی": "company",
    "آرڈر": "order", "بینک": "bank", "پیمنٹ": "payment",
    "ایڈوانس": "advance", "فراڈ": "fraud",
    "سپر وائزر": "supervisor", "مینیجر": "manager",
}

def normalize_urdu_stt(text: str) -> str:
    for urdu_word, english_word in _URDU_ENGLISH_MAP.items():
        text = text.replace(urdu_word, english_word)
    return text

# ── Urdu TTS — REST API → WAV_22050_16 → ffmpeg soxr → 16kHz PCM ────────────
_phrase_config_id: Optional[str] = None

async def text_to_speech(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        payload = {
            "voiceId": cfg.uplifts_tts_voice_id,
            "text": text,
            "outputFormat": "WAV_22050_16",
        }
        if _phrase_config_id:
            payload["phraseReplacementConfigId"] = _phrase_config_id
        r = await client.post(
            UPLIFT_TTS_REST_URL,
            json=payload,
            headers={"Authorization": f"Bearer {cfg.uplifts_tts_api_key}"},
        )
        r.raise_for_status()
        raw = r.content
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", "16000",
        "-af", "aresample=16000:resampler=soxr:precision=28",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    pcm, _ = await proc.communicate(raw)
    return pcm

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
        self.caller_gender  = ""
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

    async def _detect_gender(self, text: str):
        if self.caller_gender:
            return
        gender = detect_caller_gender(text)
        if gender:
            self.caller_gender = gender
            log.info(f"[{self.call_id}] Detected caller gender: {gender}")
            gender_note = (
                f"\n\n## Caller gender\n"
                f"Caller is {gender}. Address them using {'masculine' if gender == 'male' else 'feminine'} Urdu verb forms:\n"
                f"- {'رہے ہیں, چاہتے ہیں, بتا رہے ہیں, گئے, آئے' if gender == 'male' else 'رہی ہیں, چاہتی ہیں, بتا رہی ہیں, گئیں, آئیں'}\n"
                f"CRITICAL: Use these forms consistently throughout the conversation."
            )
            self.caller_context += gender_note

    async def play_audio(self, pcm: bytes):
        self.barge_in.clear()
        chunk = 640
        i = 0
        while i < len(pcm):
            if self.barge_in.is_set():
                log.info(f"[{self.call_id}] Barge-in — stopping playback")
                break
            frame = pcm[i:i + chunk].ljust(chunk, b'\x00')
            self.writer.write(pack_frame(AS_AUDIO_SLIN16, frame))
            await self.writer.drain()
            i += chunk
            await asyncio.sleep(0.018)

    async def speak(self, text: str):
        log.info(f"[{self.call_id}] SAIMA: {text}")
        self.speaking = True
        try:
            silence = b'\x00' * 640
            tts_task = asyncio.create_task(text_to_speech(text))
            while not tts_task.done():
                self.writer.write(pack_frame(AS_AUDIO_SLIN16, silence))
                await self.writer.drain()
                await asyncio.sleep(0.018)
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
            "معذرت، ابھی سب lines busy ہیں — "
            "آپ کا نام اور نمبر note کر لیتی ہوں، senior representative جلد از جلد call کریں گے۔ "
            "نام بتائیں؟"
        )

    async def handle_transcript(self, text: str):
        text = normalize_urdu_stt(text)
        if not text.strip() or self.thinking or self.transfer_in_progress:
            return
        if self.speaking:
            self.barge_in.set()
            await asyncio.sleep(0.05)
            return
        self.thinking = True
        log.info(f"[{self.call_id}] USER: {text}")
        self.conversation.append({"role": "user", "content": text})
        reply = "معذرت، تکنیکی مسئلہ آ رہا ہے۔ کیا آپ دوبارہ بتا سکتے ہیں؟"
        llm_task = asyncio.create_task(llm_respond(self.conversation, SYSTEM_PROMPT, cfg, self.caller_context))
        if FILLER_AUDIOS:
            await self.play_audio(random.choice(FILLER_AUDIOS))
        action = None
        try:
            reply = await llm_task
            if not reply:
                reply = "معذرت، آپ کی بات سمجھ نہیں آئی۔ دوبارہ بتائیں؟"
            else:
                spoken, action = parse_transfer_tag(reply)
                reply = spoken
                self.conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            log.error(f"[{self.call_id}] LLM error: {e}")
            llm_task.cancel()
            if self.conversation and self.conversation[-1]["role"] == "user":
                self.conversation.pop()

        asyncio.create_task(self._capture_name_phone(text))
        asyncio.create_task(self._detect_gender(text))

        if action == cfg.ext_supervisor:
            await self.speak(reply)
            await self.do_blind_transfer(cfg.ext_supervisor, self.stop_event)
            self.thinking = False
            return

        await self.speak(reply)
        self.thinking = False
        if is_farewell_response(reply, "ur"):
            if self.offered_goodbye:
                await asyncio.sleep(0.5)
                if self.stop_event:
                    self.stop_event.set()
            else:
                self.offered_goodbye = True
                await self.speak("کیا میں آپ کی اور کوئی مدد کر سکتی ہوں؟")
                self.thinking = False

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
                    f"## واپس آنے والے کلائنٹ\n"
                    f"نام: {crm['name']}\n"
                    f"فون: {caller_phone}\n"
                    f"پچھلی کالز: {crm['total_calls']}\n\n"
                    f"پچھلی گفتگو کی تاریخ (تازہ ترین پہلے):\n"
                    f"{crm['history']}\n\n"
                    f"اس معلومات کو استعمال کرکے گفتگو کو ذاتی بنائیں۔ "
                    f"انہیں نام سے مخاطب کریں۔ پچھلی دلچسپیوں کا قدرتی طریقے سے ذکر کریں۔ "
                    f"جو معلومات پہلے سے موجود ہے وہ دوبارہ نہ پوچھیں۔"
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
                        if text and self.speaking and not self.barge_in.is_set() and len(text.split()) >= 3:
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
                greeting = (
                    f"السلام علیکم {self.caller_name} صاحب! پنجاب سہولت بازار اتھارٹی میں دوبارہ خوش آمدید۔ "
                    f"میں سائمہ ہوں۔ آج میں آپ کی کیا مدد کر سکتی ہوں؟"
                )
            else:
                greeting = (
                    "السلام علیکم! میں سائمہ بول رہی ہوں PSBA ہیلپ لائن سے۔ "
                    "آپ کی کیا مدد کر سکتی ہوں؟"
                )
            tts_task = asyncio.create_task(text_to_speech(greeting))
            while not tts_task.done():
                self.writer.write(pack_frame(AS_AUDIO_SLIN16, silence))
                await self.writer.drain()
                await asyncio.sleep(0.018)
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
                log.info(f"[{self.call_id}] Deepgram Nova-3 ur connected")

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
    global _phrase_config_id
    await _ami.connect()
    await _pregenerate_fillers()
    _phrase_config_id = await create_phrase_replacement_config(cfg.uplifts_tts_api_key)
    log.info("=" * 60)
    log.info(f"PSBA — {cfg.agent_name} (Urdu, ext 8000)")
    log.info(f"  AudioSocket : {cfg.ami_host}:{cfg.audiosocket_port}")
    log.info(f"  LLM         : OpenAI {cfg.openai_model}")
    log.info(f"  STT         : Deepgram Nova-3 ur (streaming)")
    log.info(f"  TTS         : Uplift REST voice={cfg.uplifts_tts_voice_id}")
    log.info(f"  Phrase cfg  : {_phrase_config_id or 'none'}")
    log.info(f"  KB size     : {len(KNOWLEDGE_BASE):,} chars")
    log.info("=" * 60)

    server = await asyncio.start_server(
        handle_connection, cfg.ami_host, cfg.audiosocket_port
    )
    log.info(f"Listening on port {cfg.audiosocket_port} — dial 8000 to talk to Saima")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
