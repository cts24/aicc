#!/usr/bin/env python3
"""
PSBA AI Receptionist — Zara (Bilingual, ext 5000)
- Bilingual English/Urdu receptionist
- Routes to: Sara (9000), Saima (8000), Accounts (2000), Support (4000)
- Attended transfer to Supervisor (3000) via AMI — with voice whisper announcement
- ntfy alert to supervisor when unavailable
"""

import asyncio
import json
import logging
import random
import re
import smtplib
import struct
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import httpx
import websockets

from agent_lib import (
    AgentConfig, load_env, load_zara_config,
    setup_log,
    AS_HANGUP, AS_UUID, AS_AUDIO, AS_AUDIO_SLIN16, AS_ERROR, pack_frame, read_frame, downsample_16k_to_8k,
    AMIClient,
)

load_env()
cfg: AgentConfig = load_zara_config()

log = setup_log(__name__)

# ── Agent-specific constants ──────────────────────────────────────────────────
DEEPGRAM_API_KEY = cfg.deepgram_api_key
OPENAI_API_KEY = cfg.openai_api_key
OPENAI_URL = cfg.openai_url
OPENAI_MODEL = cfg.openai_model
ELEVENLABS_API_KEY = cfg.elevenlabs_api_key
ELEVENLABS_VOICE_ID = cfg.elevenlabs_voice_id
ELEVENLABS_MODEL = cfg.elevenlabs_model
SAMPLE_RATE = 16000

# ── Extension Routing ─────────────────────────────────────────────────────────
EXT_SARA       = "9000"
EXT_SAIMA      = "8000"
EXT_ACCOUNTS   = "2000"
EXT_SUPERVISOR = "3000"
EXT_SUPPORT    = "4000"
TRANSFER_CONTEXT = "from-internal"
OWNER_EXT      = "1000"

# ── Notifications ─────────────────────────────────────────────────────────────
SUPERVISOR_NTFY = "psba_supervisor"
NTFY_SERVER     = cfg.ntfy_server

# ── Gmail (for owner reports) ─────────────────────────────────────────────────
GMAIL_SENDER   = cfg.gmail_sender
GMAIL_PASSWORD = cfg.gmail_password
OWNER_EMAIL    = "haaider@live.com"

# ── Supervisor whisper announcement ───────────────────────────────────────────
SUPERVISOR_ANNOUNCE_DIR = "/opt/aiagent/sounds"
SUPERVISOR_ANNOUNCE_WAV = "/opt/aiagent/sounds/zara-supervisor-announce.wav"
SUPERVISOR_ANNOUNCE_TEXT = (
    "Hello! This is Zara, the AI receptionist at PSBA — Punjab Sahulaat Bazaars Authority. "
    "You have an inbound caller requesting to speak with a supervisor. "
    "Please press 1 now to accept the call, or simply hang up to decline."
)

# ── Deepgram STT — Nova-3 multilingual ───────────────────────────────────────
DEEPGRAM_STT_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16"
    "&sample_rate=8000"
    "&channels=1"
    "&model=nova-3"
    "&language=multi"
    "&punctuate=true"
    "&endpointing=300"
    "&utterance_end_ms=1000"
    "&interim_results=true"
    "&vad_events=true"
)

# ── Deepgram TTS (English) ────────────────────────────────────────────────────
DEEPGRAM_TTS_URL = (
    "https://api.deepgram.com/v1/speak"
    "?model=aura-asteria-en"
    "&encoding=linear16"
    f"&sample_rate={SAMPLE_RATE}"
    "&container=none"
)

# ── WAV utility ───────────────────────────────────────────────────────────────
def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    num_channels    = 1
    bits_per_sample = 16
    byte_rate    = sample_rate * num_channels * bits_per_sample // 8
    block_align  = num_channels * bits_per_sample // 8
    data_size    = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size,
        b"WAVE",
        b"fmt ", 16,
        1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + pcm_bytes

# ── Urdu TTS Phonetic Normaliser ─────────────────────────────────────────────
_PHONETIC_UR = [
    (re.compile(r'\bCustomer\s*Service\b', re.IGNORECASE), 'کسٹمر سروس'),
    (re.compile(r'\bPSBA\b', re.IGNORECASE),               'پی ایس بی اے'),
    (re.compile(r'\bSahulat\s*Bazaar\b', re.IGNORECASE),   'سہولت بازار'),
    (re.compile(r'\bBazaar\b', re.IGNORECASE),             'بازار'),
    (re.compile(r'\bWhatsApp\b', re.IGNORECASE),           'واٹس ایپ'),
    (re.compile(r'\bdelivery\b', re.IGNORECASE),           'ڈیلیوری'),
    (re.compile(r'\bhelpline\b', re.IGNORECASE),           'ہیلپ لائن'),
    (re.compile(r'براہِ\s*کرم'),                            'پلیز'),
    (re.compile(r'براہ\s*کرم'),                             'پلیز'),
]

def zara_urdu_phonetic(text: str) -> str:
    for pattern, replacement in _PHONETIC_UR:
        text = pattern.sub(replacement, text)
    return text

# ── TTS: bilingual ────────────────────────────────────────────────────────────
async def tts_urdu(text: str) -> bytes:
    text = zara_urdu_phonetic(text)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            json={
                "text": text,
                "model_id": ELEVENLABS_MODEL,
                "voice_settings": {
                    "stability": 0.4,
                    "similarity_boost": 0.85,
                    "style": 0.35,
                    "use_speaker_boost": True,
                },
            },
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        mp3_bytes = r.content
    if not mp3_bytes:
        raise RuntimeError("ElevenLabs returned empty audio")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", "pipe:0",
        "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pcm, _ = await proc.communicate(mp3_bytes)
    except Exception:
        try:
            proc.terminate()
            await asyncio.sleep(0.1)
            proc.kill()
        except Exception:
            pass
        raise
    return pcm

async def tts_english(text: str) -> bytes:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    DEEPGRAM_TTS_URL,
                    headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"},
                    json={"text": text}
                )
                r.raise_for_status()
                return r.content
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)

async def text_to_speech(text: str, lang: str = "en") -> bytes:
    if lang == "ur":
        return await tts_urdu(text)
    return await tts_english(text)

# ── Supervisor whisper announcement — generated once at startup ───────────────
async def generate_supervisor_announcement() -> None:
    try:
        Path(SUPERVISOR_ANNOUNCE_DIR).mkdir(parents=True, exist_ok=True)
        pcm = await tts_english(SUPERVISOR_ANNOUNCE_TEXT)
        wav = _pcm_to_wav(pcm)
        with open(SUPERVISOR_ANNOUNCE_WAV, "wb") as f:
            f.write(wav)
        import os
        os.chmod(SUPERVISOR_ANNOUNCE_WAV, 0o644)
        log.info(f"Supervisor announcement WAV saved: {SUPERVISOR_ANNOUNCE_WAV}")
    except Exception as e:
        log.warning(f"Could not generate supervisor announcement: {e}")

# ── LLM — English system prompt ───────────────────────────────────────────────
SYSTEM_PROMPT_EN = """You are Zara, the professional AI receptionist at PSBA — Punjab Sahulaat Bazaars Authority, a Government of Punjab statutory body. PSBA operates regulated Sahulat Bazaars across Punjab providing essential commodities at government-notified prices. You are warm, confident, and human — not robotic.

━━━ CONVERSATION FLOW ━━━
Turn 1 — Greeting already played. Ask how you can help: "How can I help you today?"
Turn 2 — Understand their reason fully. Do NOT route yet if unclear.
Turn 3 — Route OR answer once you understand their reason.
NEVER route without knowing the reason.

━━━ ROUTING RULES ━━━
Bazaar location / products / prices / shopping / app / delivery / stall inquiry / vendor registration / registration / sales / sales team / sales agent / customer service / speak to sales / talk to sales:
→ Route to English: "Great! Let me connect you with our sales team. [TRANSFER:SARA]"
→ Route to Urdu: "بہت اچھا! آپ کو ہماری سیلز ٹیم سے connect کرتی ہوں۔ [TRANSFER:SAIMA]"

Account / billing / payment / invoice / receipt / financial:
→ "Let me connect you with our accounts department. [TRANSFER:ACCOUNTS]"

Complaint / support / refund / issue / problem / defective / not working:
→ "I'll transfer you to our support team. [TRANSFER:SUPPORT]"

Supervisor / manager / human agent / human representative / complaint escalation / urgent / emergency:
→ "Let me connect you with a senior representative. [TRANSFER:SUPERVISOR]"

━━━ KEY HANDLING ━━━
- For simple questions that you CAN answer without routing: answer directly. Do NOT route every call.
- If caller is angry or irate: stay calm, acknowledge their frustration, then route to SUPPORT or SUPERVISOR.
- For location/product/app queries: route to sales (TRANSFER:SARA or TRANSFER:SAIMA).
- "Speak to owner" / OWNER_EXT requests: "Let me connect you with the owner. [TRANSFER:SUPERVISOR]"
- Never route without a clear reason. If unsure, ask: "Could you tell me a bit more about what you're looking for today?"
- Keep responses short — max 2 sentences before the transfer tag.

━━━ FAREWELL ━━━
If the caller just wants basic info and you can answer it without routing: "Thank you for calling PSBA! Goodbye."
For anything requiring action: route.

━━━ LANGUAGE SWITCH ━━━
If the caller switches to Urdu during conversation, respond in Urdu naturally.
The greeting language determines the default — but follow the caller's lead.

━━━ TAG FORMAT (CRITICAL) ━━━
Always place transfer tags at the END of your response:
WRONG: "[TRANSFER:SARA] Let me connect you."
CORRECT: "Let me connect you. [TRANSFER:SARA]"
"""

SYSTEM_PROMPT_UR = """آپ زارہ ہیں — پنجاب سہولت بازار اتھارٹی (PSBA) کی پیشہ ور AI ریسپشنسٹ۔ آپ گرم جوش، پراعتماد اور قدرتی انداز میں بات کرتی ہیں۔

━━━ بات چیت کا بہاؤ ━━━
Turn 1 — Greeting already played. Welcome and ask: "آپ کی کیا مدد کر سکتی ہوں؟"
Turn 2 — وجہ پوری طرح سمجھیں۔ جب تک واضح نہ ہو، route نہ کریں۔
Turn 3 — Route کریں یا جواب دیں۔

━━━ ROUTING RULES ━━━
Sahulat Bazaar / location / product / price / shopping / app / delivery / stall / registration / sale:
→ "بہت اچھا! آپ کو ہماری سیلز ٹیم سے connect کرتی ہوں۔ [TRANSFER:SAIMA]"
(English caller: "Let me connect you with our sales team. [TRANSFER:SARA]")

Account / billing / payment / invoice:
→ "آپ کو accounts department سے connect کرتی ہوں۔ [TRANSFER:ACCOUNTS]"

Complaint / support / refund / issue / problem / defective:
→ "آپ کو support ٹیم سے connect کرتی ہوں۔ [TRANSFER:SUPPORT]"

Supervisor / manager / human / complaint escalation / fraud / legal / urgent:
→ "آپ کو senior representative سے connect کرتی ہوں۔ [TRANSFER:SUPERVISOR]"

━━━ KEY HANDLING ━━━
- Simple questions you can answer: answer directly. Don't route everything.
- Angry caller: stay calm, acknowledge, route to SUPPORT or SUPERVISOR.
- Speak to owner: route to SUPERVISOR.
- Never route without a clear reason.
- Keep responses short — max 2 sentences before transfer tag.

━━━ LANGUAGE ━━━
Standard Pakistani Urdu. Warm, professional, feminine verb forms (بتا رہی ہوں, کر سکتی ہوں).
Never mix English and Urdu romanized in the same sentence.

━━━ TAG FORMAT ━━━
Always place transfer tags at the END: [TRANSFER:SAIMA]
"""

# ── ntfy Supervisor Alert ─────────────────────────────────────────────────────
async def notify_supervisor(caller_info: dict) -> None:
    if not SUPERVISOR_NTFY:
        return
    try:
        body = (
            f"Caller requested supervisor - unavailable after 2 attempts\n\n"
            f"Name:   {caller_info.get('name') or 'Unknown'}\n"
            f"Phone:  {caller_info.get('phone') or 'Unknown'}\n"
            f"Reason: {caller_info.get('reason') or '-'}\n"
            f"Notes:  {caller_info.get('notes') or '-'}\n"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{NTFY_SERVER}/{SUPERVISOR_NTFY}",
                content=body.encode("utf-8"),
                headers={
                    "Title": "Supervisor Callback Needed - PSBA",
                    "Priority": "high",
                    "Tags": "telephone,warning",
                }
            )
        log.info("Supervisor ntfy sent")
    except Exception as e:
        log.warning(f"Supervisor ntfy failed: {e}")

# ── Owner Report ───────────────────────────────────────────────────────────────
async def generate_and_send_report() -> str:
    report_lines = [
        f"PSBA AI - Daily Report",
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')} PKT",
        "",
        "Agents running:",
        "  - Sara (ext 9000) - English",
        "  - Saima (ext 8000) - Urdu",
        "  - Zara (ext 5000) - Receptionist",
        "",
        "For full call logs and transcripts, visit:",
        "  http://44.194.44.98:3000",
    ]
    report = "\n".join(report_lines)

    if GMAIL_SENDER and GMAIL_PASSWORD:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"PSBA AI Report - {datetime.now().strftime('%d %b %Y')}"
            msg["From"] = GMAIL_SENDER
            msg["To"] = OWNER_EMAIL
            html = f"<pre style='font-family:monospace'>{report}</pre>"
            msg.attach(MIMEText(html, "html"))
            loop = asyncio.get_event_loop()
            def _send():
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                    s.login(GMAIL_SENDER, GMAIL_PASSWORD)
                    s.sendmail(GMAIL_SENDER, OWNER_EMAIL, msg.as_string())
            await loop.run_in_executor(None, _send)
            log.info(f"Report emailed to {OWNER_EMAIL}")
            return "I've sent the report to your email."
        except Exception as e:
            log.warning(f"Report email failed: {e}")
            return "Sorry, I couldn't send the report right now."

    return "Email not configured. Please check the Chatwoot dashboard for call reports."

# ── Transfer intent detection ─────────────────────────────────────────────────
TRANSFER_TAGS = {
    "[TRANSFER:SARA]":       EXT_SARA,
    "[TRANSFER:SAIMA]":      EXT_SAIMA,
    "[TRANSFER:ACCOUNTS]":   EXT_ACCOUNTS,
    "[TRANSFER:SUPERVISOR]": EXT_SUPERVISOR,
    "[TRANSFER:SUPPORT]":    EXT_SUPPORT,
}

def parse_transfer_tag(text: str) -> tuple:
    for tag, ext in TRANSFER_TAGS.items():
        if tag in text:
            return text.replace(tag, "").strip(), ext
    return text, None

def detect_language(text: str) -> str:
    text_lower = text.lower()
    if "urdu" in text_lower or "urdoo" in text_lower:
        return "ur"
    for ch in text:
        if "\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f":
            return "ur"
        if "\u0900" <= ch <= "\u097f":
            return "ur"
    return "en"

# ── Hold Music ────────────────────────────────────────────────────────────────
_MOH_FILE: Optional[str] = None

def _find_moh_file() -> Optional[str]:
    for search in [
        "/var/lib/asterisk/moh",
        "/usr/share/asterisk/moh",
        "/opt/asterisk/var/lib/asterisk/moh",
    ]:
        p = Path(search)
        if p.exists():
            for ext in ("*.wav", "*.mp3", "*.ogg", "*.gsm"):
                files = list(p.glob(ext))
                if files:
                    return str(files[0])
    return None

async def stream_hold_music(writer, stop_event: asyncio.Event):
    global _MOH_FILE
    if _MOH_FILE is None:
        _MOH_FILE = _find_moh_file() or ""
    proc = None
    try:
        if _MOH_FILE:
            cmd = [
                "ffmpeg", "-stream_loop", "-1", "-i", _MOH_FILE,
                "-f", "s16le", "-ar", "16000", "-ac", "1",
                "-loglevel", "quiet", "pipe:1",
            ]
        else:
            cmd = [
                "ffmpeg",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                "-f", "s16le", "-ar", "16000", "-ac", "1",
                "-loglevel", "quiet", "pipe:1",
            ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        chunk = 640
        chunk_duration = 320 / 16000
        next_tick = time.monotonic()
        while not stop_event.is_set():
            data = await proc.stdout.read(chunk)
            if not data:
                break
            frame = data.ljust(chunk, b'\x00')
            writer.write(pack_frame(AS_AUDIO_SLIN16, frame))
            await writer.drain()
            next_tick += chunk_duration
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning(f"Hold music error: {e} — switching to silence")
        next_tick = time.monotonic()
        while not stop_event.is_set():
            try:
                writer.write(pack_frame(AS_AUDIO_SLIN16, b'\x00' * 640))
                await writer.drain()
                next_tick += chunk_duration
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                break
            except Exception:
                break
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

# ── Farewell detection ────────────────────────────────────────────────────────
def is_farewell_response(text: str) -> bool:
    t = text.lower()
    en_signals = [
        "goodbye", "good bye", "take care", "have a great", "have a wonderful",
        "safe travels", "all the best", "bye for now", "thank you for calling",
        "have a good day", "have a nice day",
    ]
    ur_signals = ["اللہ حافظ", "خدا حافظ", "شکریہ آپ کا", "آپ کا شکریہ", "کال کرنے کا شکریہ"]
    return any(s in t for s in en_signals) or any(s in text for s in ur_signals)


def _callback_window():
    from datetime import timezone, timedelta
    pkt_hour = datetime.now(timezone(timedelta(hours=5))).hour
    if 9 <= pkt_hour < 13:
        return "within the next 30 minutes", "اگلے 30 منٹ میں"
    if 13 <= pkt_hour < 18:
        return "before close of business today", "آج کاروباری اوقات ختم ہونے سے پہلے"
    return "first thing tomorrow morning at 9 AM", "کل صبح 9 بجے"

# ── Call Handler ──────────────────────────────────────────────────────────────
_ami = AMIClient(cfg)

class ZaraCallHandler:
    def __init__(self, reader, writer):
        self.reader          = reader
        self.writer          = writer
        self.call_id         = "unknown"
        self.lang            = "en"
        self.thinking        = False
        self.speaking        = False
        self.barge_in        = asyncio.Event()
        self.conversation    = []
        self.farewell_said   = False
        self.stop_event      = None
        self.transfer_in_progress = False
        self.supervisor_attempted = False
        self.asterisk_channel  = None
        self.callback_info     = {"name": "", "phone": "", "reason": "", "notes": ""}

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT_UR if self.lang == "ur" else SYSTEM_PROMPT_EN

    async def play_audio(self, pcm: bytes):
        self.barge_in.clear()
        chunk = 640
        chunk_duration = 320 / 16000
        next_tick = time.monotonic()
        i = 0
        while i < len(pcm):
            if self.barge_in.is_set():
                break
            frame = pcm[i:i + chunk].ljust(chunk, b'\x00')
            self.writer.write(pack_frame(AS_AUDIO_SLIN16, frame))
            await self.writer.drain()
            i += chunk
            next_tick += chunk_duration
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def speak(self, text: str, lang: str = ""):
        use_lang = lang or self.lang
        log.info(f"[{self.call_id}] ZARA ({use_lang}): {text}")
        self.speaking = True
        try:
            silence = b'\x00' * 640
            chunk_duration = 320 / 16000
            next_tick = time.monotonic()
            tts_task = asyncio.create_task(text_to_speech(text, use_lang))
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

    async def llm_respond(self, conversation: list) -> str:
        system = self._system_prompt()
        messages = [{"role": "system", "content": system}] + conversation[-10:]
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(OPENAI_URL, json={
                        "model": OPENAI_MODEL,
                        "messages": messages,
                        "max_tokens": 150,
                        "temperature": 0.65,
                    }, headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    })
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                if attempt == 2:
                    raise
                wait = 5 if "429" in str(e) else 0.5
                log.warning(f"LLM attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(wait)

    async def handle_transcript(self, text: str):
        if not text.strip() or self.thinking or self.transfer_in_progress:
            return
        if self.speaking:
            self.barge_in.set()
            await asyncio.sleep(0.05)
        self.thinking = True
        log.info(f"[{self.call_id}] USER ({self.lang}): {text}")

        # First meaningful input — detect language if not yet confirmed
        if not self.conversation:
            detected = detect_language(text)
            if detected == "ur" and self.lang == "en":
                self.lang = "ur"
                self.conversation.clear()
                await self.speak("جی، بتائیں — آپ کی کیا مدد کر سکتی ہوں؟", "ur")
                self.thinking = False
                return
            elif detected == "en" and self.lang == "ur":
                self.lang = "en"
                self.conversation.clear()
                await self.speak("Sure! How can I help you today?", "en")
                self.thinking = False
                return

        self.conversation.append({"role": "user", "content": text})
        llm_task = asyncio.create_task(self.llm_respond(self.conversation))
        reply = "Sorry, I didn't catch that. Could you repeat?"
        try:
            reply = await llm_task
            if not reply:
                reply = "Sorry, could you repeat that?"
            else:
                self.conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            log.error(f"[{self.call_id}] LLM error: {e}")
        finally:
            self.thinking = False

        spoken, action = parse_transfer_tag(reply)
        if action:
            if self.transfer_in_progress:
                return
            self.transfer_in_progress = True
            log.info(f"[{self.call_id}] Transfer requested: {action} for ext {action}")
            await self.speak(spoken)
            await self.do_attended_transfer(action)
            self.transfer_in_progress = False
            return

        await self.speak(spoken)
        if is_farewell_response(spoken):
            if self.farewell_said:
                await asyncio.sleep(0.5)
                if self.stop_event:
                    self.stop_event.set()
            else:
                self.farewell_said = True
                cb_en, cb_ur = _callback_window()
                if self.lang == "ur":
                    await self.speak("کیا میں آپ کی اور کوئی مدد کر سکتی ہوں؟", "ur")
                else:
                    await self.speak("Is there anything else I can help you with today?", "en")

    async def do_attended_transfer(self, exten: str):
        """Attended transfer: hold → Originate supervisor → bridge on answer."""
        await asyncio.sleep(0.5)
        hold_stop = asyncio.Event()
        hold_task = asyncio.create_task(stream_hold_music(self.writer, hold_stop))

        if not self.asterisk_channel:
            self.asterisk_channel = await _ami.get_agent_channel()
            if self.asterisk_channel:
                log.info(f"[{self.call_id}] Late channel scan: {self.asterisk_channel}")
            else:
                log.warning(f"[{self.call_id}] No Asterisk channel found")

        if not self.asterisk_channel:
            hold_stop.set()
            await hold_task
            fallback = "All lines are busy. Please call again later." if self.lang == "en" else "سب lines busy ہیں. براہ کرم بعد میں call کریں۔"
            await self.speak(fallback, self.lang)
            return

        if exten == EXT_SUPERVISOR and self.supervisor_attempted:
            hold_stop.set()
            await hold_task
            await self._handle_callback()
            return

        action_id = await _ami.originate_supervisor_check(self.asterisk_channel, exten)
        if not action_id:
            hold_stop.set()
            await hold_task
            fallback = "I can't connect you right now. Let me take your details." if self.lang == "en" else "ابھی connect نہیں کر سکتی — آپ کا نام اور نمبر لے لیتی ہوں۔"
            await self.speak(fallback, self.lang)
            return

        log.info(f"[{self.call_id}] Waiting for supervisor to answer (timeout 25s)...")
        supervisor_done = await _ami.wait_for_originate_response(action_id, timeout=25.0)

        hold_stop.set()
        await hold_task

        if supervisor_done:
            log.info(f"[{self.call_id}] Supervisor answered or call ended — waiting for Asterisk HANGUP")
            if self.stop_event:
                self.stop_event.set()
        else:
            log.warning(f"[{self.call_id}] Supervisor did not answer")
            if exten == EXT_SUPERVISOR:
                if not self.supervisor_attempted:
                    self.supervisor_attempted = True
                    await asyncio.sleep(2)
                    await self.do_attended_transfer(exten)
                    return
                await self._handle_callback()

    async def _handle_callback(self):
        cb_en, cb_ur = _callback_window()
        if self.lang == "ur":
            await self.speak(
                "معذرت، اس وقت ہمارے senior representative دستیاب نہیں ہیں۔ "
                "آپ کا نام اور نمبر لے لیتی ہوں — وہ آپ سے " + cb_ur + " رابطہ کریں گے۔ نام بتائیں؟",
                "ur"
            )
        else:
            await self.speak(
                "I'm sorry, our senior representatives are unavailable at the moment. "
                "I can take your name and number and they'll get back to you " + cb_en + ". May I have your name?",
                "en"
            )
        if self.stop_event:
            self.stop_event.set()

    async def capture_callback_info(self, text: str):
        """Extract name and phone from caller response during callback flow."""
        import json
        prompt = (
            "Extract name and Pakistani phone number from this text. "
            "Return JSON: {\"name\": \"...\", \"phone\": \"...\"}\n"
            f"Text: {text}"
        )
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(OPENAI_URL, json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 40, "temperature": 0,
                }, headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"})
                r.raise_for_status()
                data = json.loads(r.json()["choices"][0]["message"]["content"].strip())
                if data.get("name"):
                    self.callback_info["name"] = data["name"]
                if data.get("phone"):
                    self.callback_info["phone"] = data["phone"]
        except Exception:
            pass

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
        except asyncio.TimeoutError:
            log.warning(f"[{self.call_id}] AMI channel lookup timed out")

        dg_headers      = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
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
            en = "Hello! This is Zara, the AI receptionist at PSBA. How can I help you today?"
            ur = "السلام علیکم! میں زارہ ہوں PSBA ریسپشنسٹ۔ آپ کی کیا مدد کر سکتی ہوں؟"
            greeting = ur if self.lang == "ur" else en
            tts_task = asyncio.create_task(text_to_speech(greeting, self.lang))
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
                log.info(f"[{self.call_id}] Deepgram Nova-3 multi connected")

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

# ── Server entry point ────────────────────────────────────────────────────────
async def handle_connection(reader, writer):
    addr = writer.get_extra_info("peername")
    log.info(f"Connection from {addr}")
    handler = ZaraCallHandler(reader, writer)
    await handler.run()

async def main():
    await _ami.connect()
    await generate_supervisor_announcement()
    log.info("=" * 60)
    log.info(f"PSBA — Zara (Bilingual, ext 5000)")
    log.info(f"  AudioSocket : 0.0.0.0:{cfg.audiosocket_port}")
    log.info(f"  LLM         : OpenAI {OPENAI_MODEL}")
    log.info(f"  STT         : Deepgram Nova-3 multi")
    log.info(f"  TTS EN      : Deepgram Aura")
    log.info(f"  TTS UR      : ElevenLabs Sana")
    log.info("=" * 60)

    server = await asyncio.start_server(
        handle_connection, "0.0.0.0", cfg.audiosocket_port
    )
    log.info(f"Listening on port {cfg.audiosocket_port} — dial 5000 to talk to Zara")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
