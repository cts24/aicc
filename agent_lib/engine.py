"""AgentEngine — shared async base class for all PSBA AI voice agents.

Eliminates ~70% code duplication across saima.py, voice_agent.py, zara.py.

Usage:
    class SaimaEngine(AgentEngine):
        AGENT_NAME = "Saima"
        SYSTEM_PROMPT = "..."
        DEEPGRAM_STT_LANGUAGE = "ur"

        async def text_to_speech(self, text: str) -> bytes: ...
        def get_greeting(self) -> str: ...
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import ClassVar, Optional

import websockets

from agent_lib.config import AgentConfig
from agent_lib.audiosocket import (
    AS_HANGUP, AS_UUID, AS_AUDIO, AS_AUDIO_SLIN16, AS_ERROR,
    pack_frame, read_frame, downsample_16k_to_8k,
)
from agent_lib.ami import AMIClient, get_caller_id
from agent_lib.odoo import OdooClient
from agent_lib.speech import is_farewell_response
from agent_lib.llm import llm_respond, extract_name_phone, parse_transfer_tag
from agent_lib.chatwoot import chatwoot_lookup, create_chatwoot_lead
from agent_lib.ntfy import send_ntfy_notification
from agent_lib.gmail import send_gmail_notification
from agent_lib.calendar import (
    book_sales_appointment, get_available_slots, format_slots_for_prompt, check_and_book,
    get_bookings_by_phone, get_bookings_by_email, cancel_booking, reschedule_booking,
)
from agent_lib.llm import extract_lead_data

log = logging.getLogger(__name__)

CHUNK_SIZE = 640
CHUNK_DURATION = 320 / 16000

SILENCE_QUEUE = asyncio.Queue()


def _build_stt_url(language: str, model: str = "nova-3") -> str:
    return (
        f"wss://api.deepgram.com/v1/listen"
        f"?encoding=linear16"
        f"&sample_rate=8000"
        f"&channels=1"
        f"&model={model}"
        f"&language={language}"
        f"&punctuate=true"
        f"&endpointing=300"
        f"&utterance_end_ms=1000"
        f"&interim_results=true"
        f"&vad_events=true"
    )


def _load_hold_music(path: str) -> bytes:
    p = Path(path)
    try:
        data = p.read_bytes()
        log.info(f"Hold music loaded: {len(data)} bytes ({len(data)/16000:.1f}s)")
        return data
    except Exception as e:
        log.warning(f"Hold music load failed: {e}")
        return b""


class AgentEngine:
    """Shared base for all voice agents.

    Subclasses MUST override:
        AGENT_NAME, SYSTEM_PROMPT, DEEPGRAM_STT_LANGUAGE
        text_to_speech(), get_greeting()

    Subclasses MAY override:
        DEEPGRAM_STT_MODEL (default nova-3)
        BARGE_IN_WORD_COUNT (default 3)
        RETURN_ON_BARGE_IN (default False)
        FILLER_PHRASES (default [])
        HOLD_MUSIC_PATH (default "")
        normalize_stt(), normalize_tts(), post_call_actions()
        on_before_llm(), on_after_llm(), _on_call_setup()
        get_farewell_extra(), get_transfer_fallback()
    """

    # ── Class-level overrides ──────────────────────────────────────────────
    AGENT_NAME: ClassVar[str] = ""
    SYSTEM_PROMPT: ClassVar[str] = ""
    DEEPGRAM_STT_LANGUAGE: ClassVar[str] = "multi"
    DEEPGRAM_STT_MODEL: ClassVar[str] = "nova-3"
    BARGE_IN_WORD_COUNT: ClassVar[int] = 3
    FILLER_PHRASES: ClassVar[list[str]] = []
    RETURN_ON_BARGE_IN: ClassVar[bool] = False
    HOLD_MUSIC_PATH: ClassVar[str] = ""

    # Shared service instances (one per process, set at startup)
    ami: ClassVar[Optional[AMIClient]] = None
    odoo: ClassVar[Optional[OdooClient]] = None
    filler_audios: ClassVar[list[bytes]] = []
    hold_audio: ClassVar[bytes] = b""
    deepgram_stt_url: ClassVar[str] = ""

    # ── Per-call instance state ────────────────────────────────────────────

    def __init__(self, cfg: AgentConfig, reader, writer):
        self.cfg = cfg
        self.reader = reader
        self.writer = writer
        self.call_id = "unknown"
        self.conversation: list[dict] = []
        self.thinking = False
        self.speaking = False
        self.barge_in = asyncio.Event()
        self.caller_context = ""
        self.caller_name = ""
        self.caller_phone = ""
        self.caller_gender = ""
        self.stop_event: Optional[asyncio.Event] = None
        self.offered_goodbye = False
        self.asterisk_channel: Optional[str] = None
        self.transfer_in_progress = False
        self._system_prompt = self.SYSTEM_PROMPT
        self.lead_data: dict = {}
        self._turn_count = 0
        self._lead_extract_task: Optional[asyncio.Task] = None

    BOOKING_INSTRUCTIONS: ClassVar[str] = (
        "\n\n## APPOINTMENT BOOKING\n"
        "You can check availability and book appointments in-call.\n"
        "When caller agrees on a date and time, use:\n"
        "  [BOOKING:id=1&date=YYYY-MM-DD&time=HH:MM]\n"
        "To check what slots are available on a date, use:\n"
        "  [SLOTS:id=1&date=YYYY-MM-DD]\n"
        "\n"
        "## CANCEL / RESCHEDULE\n"
        "If caller wants to cancel or reschedule, first search by asking for their phone:\n"
        "  [SEARCH_BOOKINGS:phone=+92XXXXXXXXXX]\n"
        "System will return their bookings. Present them to caller and confirm.\n"
        "To cancel a booking:\n"
        "  [CANCEL_BOOKING:uid=BOOKING_UID]\n"
        "To reschedule, first check [SLOTS:] for availability, then:\n"
        "  [RESCHEDULE_BOOKING:uid=BOOKING_UID&date=YYYY-MM-DD&time=HH:MM]\n"
        "Confirmation will be spoken to caller after each action."
    )

    # ── Must override ──────────────────────────────────────────────────────

    async def text_to_speech(self, text: str) -> bytes:
        raise NotImplementedError

    def get_greeting(self) -> str:
        raise NotImplementedError

    # ── Fallback messages (override per language) ──────────────────────────
    FALLBACK_TECHNICAL: ClassVar[str] = "Sorry, I'm having a technical issue. Can you repeat that?"
    FALLBACK_NOT_UNDERSTOOD: ClassVar[str] = "Sorry, I didn't catch that. Could you please repeat?"

    # ── Optional overrides ─────────────────────────────────────────────────

    def normalize_stt(self, text: str) -> str:
        return text

    def normalize_tts(self, text: str) -> str:
        return text

    async def post_call_actions(self, conversation: list, call_id: str, caller_phone: str = "", complaint_id: Optional[int] = None) -> None:
        pass

    async def on_before_llm(self, text: str) -> list:
        """Called before LLM request. Return list of background coroutines."""
        return []

    async def on_after_llm(self, text: str, reply: str, action: Optional[str] = None) -> str:
        """Called after LLM responds but before speaking/transfer.
        Return modified reply string (or original if unchanged)."""
        return reply

    async def _on_call_setup(self):
        """Hook after AMI caller-id lookup, before Deepgram session."""
        agent_type = getattr(self.cfg, 'agent_type', '')
        if agent_type in ('english_sales', 'urdu_sales', 'english_support', 'urdu_support', 'receptionist'):
            self._system_prompt = self.SYSTEM_PROMPT + self.BOOKING_INSTRUCTIONS

    def get_farewell_extra(self) -> str:
        return "Is there anything else I can help you with today?"

    def get_transfer_fallback(self) -> str:
        return (
            "I'm sorry, all lines are busy right now. "
            "May I take your name and number so a senior representative can call you back?"
        )

    # ── Audio output ───────────────────────────────────────────────────────

    async def _send_silence(self):
        self.writer.write(pack_frame(AS_AUDIO_SLIN16, b'\x00' * CHUNK_SIZE))
        await self.writer.drain()

    async def play_audio(self, pcm: bytes):
        self.barge_in.clear()
        next_tick = time.monotonic()
        i = 0
        while i < len(pcm):
            if self.barge_in.is_set():
                log.info(f"[{self.call_id}] Barge-in — stopping playback")
                break
            frame = pcm[i:i + CHUNK_SIZE].ljust(CHUNK_SIZE, b'\x00')
            self.writer.write(pack_frame(AS_AUDIO_SLIN16, frame))
            await self.writer.drain()
            i += CHUNK_SIZE
            next_tick += CHUNK_DURATION
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def speak(self, text: str):
        log.info(f"[{self.call_id}] {self.AGENT_NAME}: {text}")
        tts_text = self.normalize_tts(text)
        self.speaking = True
        try:
            next_tick = time.monotonic()
            tts_task = asyncio.create_task(self.text_to_speech(tts_text))
            while not tts_task.done():
                await self._send_silence()
                next_tick += CHUNK_DURATION
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            pcm = await tts_task
            await self.play_audio(pcm)
        except Exception as e:
            log.error(f"[{self.call_id}] TTS error: {e}")
        finally:
            self.speaking = False

    # ── Deepgram WebSocket ────────────────────────────────────────────────

    async def _dg_receiver(self, ws):
        async for msg in ws:
            try:
                data = json.loads(msg)
                msg_type = data.get("type", "")
                if msg_type == "Results":
                    alt = data["channel"]["alternatives"][0]
                    text = alt.get("transcript", "")
                    barge_words = self.BARGE_IN_WORD_COUNT
                    if text and self.speaking and not self.barge_in.is_set() and len(text.split()) >= barge_words:
                        log.info(f"[{self.call_id}] Barge-in: '{text}'")
                        self.barge_in.set()
                    if text and data.get("is_final"):
                        self._transcript_parts.append(text)
                elif msg_type == "UtteranceEnd":
                    full = " ".join(self._transcript_parts).strip()
                    self._transcript_parts.clear()
                    if full:
                        asyncio.create_task(self.handle_transcript(full))
            except Exception as e:
                log.debug(f"DG parse: {e}")

    async def _dg_sender(self, ws):
        while not self.stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
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

    # ── Asterisk AudioSocket reader ───────────────────────────────────────

    async def _asterisk_reader(self):
        while not self.stop_event.is_set():
            try:
                kind, data = await asyncio.wait_for(
                    read_frame(self.reader), timeout=60
                )
                if kind in (AS_HANGUP, AS_ERROR):
                    log.info(f"[{self.call_id}] Hangup/error")
                    self.stop_event.set()
                    break
                if kind in (AS_AUDIO, AS_AUDIO_SLIN16) and data:
                    chunk = downsample_16k_to_8k(data) if kind == AS_AUDIO_SLIN16 else data
                    await self._audio_queue.put(chunk)
            except asyncio.TimeoutError:
                log.info(f"[{self.call_id}] Read timeout — ending call")
                self.stop_event.set()
                break
            except asyncio.IncompleteReadError:
                log.info(f"[{self.call_id}] Caller disconnected")
                self.stop_event.set()
                break
            except Exception as e:
                log.error(f"[{self.call_id}] Read error: {e}")
                self.stop_event.set()
                break

    # ── Name/phone capture ────────────────────────────────────────────────

    async def _capture_name_phone(self, text: str):
        try:
            name, phone = await extract_name_phone(text, self.cfg)
            if name and not self.caller_name:
                self.caller_name = name
                log.info(f"[{self.call_id}] Captured name: {name}")
            if phone and not self.caller_phone:
                self.caller_phone = phone
                log.info(f"[{self.call_id}] Captured phone: {phone}")
        except Exception:
            pass

    # ── Farewell ──────────────────────────────────────────────────────────

    async def _midcall_lead_extract(self, text: str):
        """Extract lead data mid-call and store incrementally."""
        try:
            lead = await extract_lead_data(self.conversation, self.AGENT_NAME, self.cfg)
            if lead and lead.get('name'):
                self.lead_data.update(lead)
                if lead.get('name'):
                    self.caller_name = lead['name']
                if lead.get('phone') and not self.caller_phone:
                    self.caller_phone = lead['phone']
                log.info(f"[{self.call_id}] Mid-call lead: {lead.get('name')} | {lead.get('inquiry_type')} | score {lead.get('lead_temperature')}")
        except Exception as e:
            log.debug(f"[{self.call_id}] Mid-call lead extract error: {e}")

    async def _handle_booking_action(self, reply: str) -> str:
        """Handle [SLOTS], [BOOKING], [SEARCH_BOOKINGS], [CANCEL_BOOKING],
        [RESCHEDULE_BOOKING] tags from LLM response.

        For [SLOTS] / [SEARCH_BOOKINGS]: fetches data, injects into conversation,
        re-calls LLM. Returns empty str if re-query in progress (reply already added
        to conversation).
        For [BOOKING] / [CANCEL_BOOKING] / [RESCHEDULE_BOOKING]: executes and
        returns modified reply with confirmation message.
        """
        import re

        # ── SLOTS: check availability ───────────────────────────────────────
        slots_match = re.search(r'\[SLOTS:([^\]]+)\]', reply)
        if slots_match:
            raw = slots_match.group(1)
            params = dict(p.split('=') for p in raw.split('&'))
            event_type_id = int(params.get('id', '1'))
            date_str = params.get('date', '')

            if not date_str:
                return reply.replace(slots_match.group(0), '').strip()

            slots = await get_available_slots(event_type_id, date_str)
            slots_text = format_slots_for_prompt(slots, date_str)
            info = f"[System: {slots_text}]"
            self.conversation.append({"role": "system", "content": info})

            re_reply = await llm_respond(self.conversation, self._system_prompt, self.cfg, self.caller_context)
            self.conversation.pop()
            if re_reply:
                self.conversation.append({"role": "assistant", "content": re_reply})
            return re_reply or ""

        # ── SEARCH_BOOKINGS: find existing bookings ─────────────────────────
        search_match = re.search(r'\[SEARCH_BOOKINGS:([^\]]+)\]', reply)
        if search_match:
            raw = search_match.group(1)
            params = dict(p.split('=') for p in raw.split('&'))
            phone = params.get('phone', '')
            email = params.get('email', '')

            bookings = []
            if phone:
                bookings = await get_bookings_by_phone(phone)
            elif email:
                bookings = await get_bookings_by_email(email)

            if not bookings:
                info = "[System: No upcoming bookings found for this caller.]"
            else:
                lines = []
                for b in bookings:
                    start = datetime.fromisoformat(b['start_time'])
                    date_str = start.strftime('%B %d, %Y')
                    time_str = start.strftime('%I:%M %p').lstrip('0')
                    lines.append(f"  UID: {b['uid'][:8]}… — {date_str} at {time_str} ({b['title']})")
                info = "[System: Found these upcoming bookings:\n" + "\n".join(lines) + "\n]"

            self.conversation.append({"role": "system", "content": info})
            re_reply = await llm_respond(self.conversation, self._system_prompt, self.cfg, self.caller_context)
            self.conversation.pop()
            if re_reply:
                self.conversation.append({"role": "assistant", "content": re_reply})
            return re_reply or ""

        # ── BOOKING: create new booking ────────────────────────────────────
        booking_match = re.search(r'\[BOOKING:([^\]]+)\]', reply)
        if booking_match:
            raw = booking_match.group(1)
            params = dict(p.split('=') for p in raw.split('&'))
            event_type_id = int(params.get('id', '1'))
            date_str = params.get('date', '')
            time_str = params.get('time', '')
            name = params.get('name', self.caller_name or 'Caller')
            email = params.get('email', 'caller@psba.gov.pk')
            phone = params.get('phone', self.caller_phone or '')

            if not date_str or not time_str:
                return reply.replace(booking_match.group(0),
                    "I need a date and time to make the booking.").strip()

            success, msg = await check_and_book(event_type_id, date_str, time_str, name, email, phone)
            if success:
                confirm = f"Perfect! I've booked your appointment for {date_str} at {time_str}. You will receive a confirmation."
                return reply.replace(booking_match.group(0), confirm).strip()
            else:
                return reply.replace(booking_match.group(0),
                    f"Sorry, I couldn't complete the booking: {msg}").strip()

        # ── CANCEL_BOOKING ─────────────────────────────────────────────────
        cancel_match = re.search(r'\[CANCEL_BOOKING:([^\]]+)\]', reply)
        if cancel_match:
            raw = cancel_match.group(1)
            params = dict(p.split('=') for p in raw.split('&'))
            uid = params.get('uid', '')

            if not uid:
                return reply.replace(cancel_match.group(0), '').strip()

            cancelled = await cancel_booking(uid)
            if cancelled:
                confirm = "Done! Your appointment has been cancelled successfully."
                return reply.replace(cancel_match.group(0), confirm).strip()
            else:
                return reply.replace(cancel_match.group(0),
                    "I'm sorry, I was unable to cancel the booking. Please try again.").strip()

        # ── RESCHEDULE_BOOKING ────────────────────────────────────────────
        resched_match = re.search(r'\[RESCHEDULE_BOOKING:([^\]]+)\]', reply)
        if resched_match:
            raw = resched_match.group(1)
            params = dict(p.split('=') for p in raw.split('&'))
            uid = params.get('uid', '')
            date_str = params.get('date', '')
            time_str = params.get('time', '')

            if not uid or not date_str or not time_str:
                return reply.replace(resched_match.group(0),
                    "I need the booking details and the new time to reschedule.").strip()

            new_start = f"{date_str}T{time_str}:00+05:00"
            result = await reschedule_booking(uid, new_start)
            if "error" in result:
                return reply.replace(resched_match.group(0),
                    f"Sorry, rescheduling failed: {result['error']}").strip()
            else:
                confirm = f"Done! Your appointment has been rescheduled to {date_str} at {time_str}."
                return reply.replace(resched_match.group(0), confirm).strip()

        return reply

    # ── Transfer ──────────────────────────────────────────────────────────

    async def do_blind_transfer(self, exten: str):
        self.transfer_in_progress = True
        self.thinking = True
        try:
            if self.hold_audio:
                log.info(f"[{self.call_id}] Playing hold music ({len(self.hold_audio)} bytes)")
                await self.play_audio(self.hold_audio)
            else:
                await asyncio.sleep(2.0)
            if not self.asterisk_channel:
                self.asterisk_channel = await self.ami.get_agent_channel()
                if self.asterisk_channel:
                    log.info(f"[{self.call_id}] Late channel scan found: {self.asterisk_channel}")
        finally:
            self.thinking = False
            self.transfer_in_progress = False

        if self.asterisk_channel:
            success = await self.ami.blind_transfer(self.asterisk_channel, exten)
            if success:
                log.info(f"[{self.call_id}] Redirected to ext {exten}")
                return
            log.warning(f"[{self.call_id}] AMI Redirect to {exten} failed")
        else:
            log.warning(f"[{self.call_id}] No Asterisk channel — cannot transfer")

        await self.speak(self.get_transfer_fallback())

    # ── Transcript handling (base) ────────────────────────────────────────

    async def handle_transcript(self, text: str):
        text = self.normalize_stt(text)
        if not text.strip() or self.thinking or self.transfer_in_progress:
            return
        if self.speaking:
            self.barge_in.set()
            await asyncio.sleep(0.05)
            if self.RETURN_ON_BARGE_IN:
                return
        self.thinking = True
        log.info(f"[{self.call_id}] USER: {text}")
        self.conversation.append({"role": "user", "content": text})

        bgtasks = await self.on_before_llm(text)

        llm_task = asyncio.create_task(
            llm_respond(self.conversation, self._system_prompt, self.cfg, self.caller_context)
        )
        if self.filler_audios:
            await self.play_audio(random.choice(self.filler_audios))

        reply = self.FALLBACK_TECHNICAL
        action = None
        try:
            reply = await llm_task
            if not reply:
                reply = self.FALLBACK_NOT_UNDERSTOOD
            else:
                spoken, action = parse_transfer_tag(reply)
                reply = spoken
                self.conversation.append({"role": "assistant", "content": reply})
        except Exception as e:
            log.error(f"[{self.call_id}] LLM error: {e}")
            llm_task.cancel()
            if self.conversation and self.conversation[-1]["role"] == "user":
                self.conversation.pop()

        for coro in bgtasks:
            asyncio.create_task(coro)

        reply = await self.on_after_llm(text, reply, action)

        if action is not None:
            await self.speak(reply)
            await self.do_blind_transfer(action)
            self.thinking = False
            return

        # ── In-call booking actions (SLOTS, BOOKING, SEARCH_BOOKINGS, CANCEL, RESCHEDULE) ──
        booking_tags = ('[SLOTS:', '[BOOKING:', '[SEARCH_BOOKINGS:', '[CANCEL_BOOKING:', '[RESCHEDULE_BOOKING:')
        if any(t in reply for t in booking_tags):
            try:
                reply = await self._handle_booking_action(reply)
            except Exception as e:
                log.error(f"[{self.call_id}] Booking action error: {e}")
                reply = "Sorry, I'm having trouble with the booking system. Please try again."

            if not reply:
                self.thinking = False
                return

        await self.speak(reply)
        self.thinking = False
        await self._check_farewell(reply)

        # ── Mid-call lead extraction (every 4 turns) ────────────────────
        self._turn_count += 1
        if self._turn_count >= 4 and (self._lead_extract_task is None or self._lead_extract_task.done()):
            self._turn_count = 0
            self._lead_extract_task = asyncio.create_task(self._midcall_lead_extract(text))

    # ── Greeting ──────────────────────────────────────────────────────────

    async def greeting_task(self):
        await asyncio.sleep(0.3)
        self.thinking = True
        greeting = self.get_greeting()
        next_tick = time.monotonic()
        tts_task = asyncio.create_task(self.text_to_speech(greeting))
        while not tts_task.done():
            await self._send_silence()
            next_tick += CHUNK_DURATION
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

    # ── Main run loop ─────────────────────────────────────────────────────

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
            self.asterisk_channel = await asyncio.wait_for(self.ami.get_agent_channel(), timeout=4)
            if self.asterisk_channel:
                log.info(f"[{self.call_id}] Asterisk channel: {self.asterisk_channel}")
        except asyncio.TimeoutError:
            log.warning(f"[{self.call_id}] AMI channel lookup timed out")

        try:
            self.caller_phone = await asyncio.wait_for(get_caller_id(self.cfg), timeout=3)
        except asyncio.TimeoutError:
            self.caller_phone = ""
            log.warning(f"[{self.call_id}] AMI caller ID lookup timed out")

        await self._on_call_setup()

        self._transcript_parts: list[str] = []
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self.stop_event = asyncio.Event()

        dg_headers = {"Authorization": f"Token {self.cfg.deepgram_api_key}"}

        try:
            async with websockets.connect(
                type(self).deepgram_stt_url,
                additional_headers=dg_headers,
                ping_interval=20,
            ) as dg_ws:
                log.info(f"[{self.call_id}] Deepgram STT connected")

                await asyncio.gather(
                    self._asterisk_reader(),
                    self._dg_receiver(dg_ws),
                    self._dg_sender(dg_ws),
                    self.greeting_task(),
                )

                try:
                    await dg_ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass

        except Exception as e:
            log.error(f"[{self.call_id}] WebSocket error: {e}")
        finally:
            self.stop_event.set()
            self.writer.close()
            log.info(f"[{self.call_id}] Call ended")
            asyncio.create_task(
                self.post_call_actions(
                    list(self.conversation), self.call_id, self.caller_phone,
                    complaint_id=getattr(self, '_complaint_id', None),
                )
            )

    # ── Pre-generation helpers ────────────────────────────────────────────

    @classmethod
    async def pregenerate_fillers(cls):
        results = []
        for phrase in cls.FILLER_PHRASES:
            try:
                pcm = await cls._static_text_to_speech(phrase)
                results.append(pcm)
            except Exception as e:
                log.warning(f"Filler pre-gen failed for '{phrase}': {e}")
        cls.filler_audios = results
        cls.hold_audio = _load_hold_music(cls.HOLD_MUSIC_PATH) if cls.HOLD_MUSIC_PATH else b""
        log.info(f"Fillers pre-generated: {len(cls.filler_audios)}/{len(cls.FILLER_PHRASES)}")

    @classmethod
    async def _static_text_to_speech(cls, text: str) -> bytes:
        """Subclass can override this static version for filler pre-gen."""
        raise NotImplementedError

    @classmethod
    def setup_services(cls, cfg: AgentConfig):
        cls.deepgram_stt_url = _build_stt_url(cls.DEEPGRAM_STT_LANGUAGE, cls.DEEPGRAM_STT_MODEL)
        cls.ami = AMIClient(cfg)
        if cfg.odoo_url and cfg.odoo_db:
            cls.odoo = OdooClient(cfg.odoo_url, cfg.odoo_db, cfg.odoo_username, cfg.odoo_password)
