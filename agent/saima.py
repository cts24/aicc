#!/usr/bin/env python3
"""
PSBA AI Voice Agent — Saima (Urdu, ext 8000)
- AudioSocket: bridges Asterisk audio
- Deepgram Nova-3: real-time STT via WebSocket (language=ur)
- OpenAI GPT-4o-mini: cloud LLM
- Uplift TTS: neural Urdu TTS (voice: helpdesk-agent)
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from agent_lib import (
    load_env, load_saima_config, setup_log,
    normalize_tts_text, detect_caller_gender,
    chatwoot_lookup, create_chatwoot_lead,
    send_ntfy_notification, send_gmail_notification, book_sales_appointment,
    extract_lead_data,
)
from agent_lib.engine import AgentEngine
from agent_lib.config import AgentConfig

load_env()
cfg: AgentConfig = load_saima_config()

log = setup_log(__name__)

# ── Pre-cached filler phrases (empty — no fillers for Urdu TTS) ──────────────
_FILLERS_UR: list[str] = []

# ── Load Knowledge Base ───────────────────────────────────────────────────────
KNOWLEDGE_BASE = cfg.kb_path.read_text() if cfg.kb_path.exists() else ""

# ── Load system prompt from file ─────────────────────────────────────────────
_SAIMA_PROMPT_PATH = Path(__file__).parent / "saima_prompt.txt"
_SAIMA_PROMPT_TEMPLATE = _SAIMA_PROMPT_PATH.read_text(encoding="utf-8") if _SAIMA_PROMPT_PATH.exists() else ""
SYSTEM_PROMPT = _SAIMA_PROMPT_TEMPLATE.replace("{KNOWLEDGE_BASE}", KNOWLEDGE_BASE)

# ── Uplift REST TTS ───────────────────────────────────────────────────────────
UPLIFT_TTS_REST_URL = "https://api.upliftai.org/v1/synthesis/text-to-speech"
PHRASE_REPLACEMENT_URL = "https://api.upliftai.org/v1/synthesis/phrase-replacement-config"

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

# ── Uplift Phrase Replacement Config ─────────────────────────────────────────
_phrase_config_id: Optional[str] = None

_PHRASE_REPLACEMENTS = [
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
    {"phrase": "app", "replacement": "ایپ"},
    {"phrase": "Play Store", "replacement": "پلے سٹور"},
    {"phrase": "App Store", "replacement": "ایپ سٹور"},
    {"phrase": "home delivery", "replacement": "ہوم ڈیلیوری"},
    {"phrase": "cash on delivery", "replacement": "کیش آن ڈیلیوری"},
    {"phrase": "complaint", "replacement": "کمپلینٹ"},
    {"phrase": "stall", "replacement": "سٹال"},
    {"phrase": "balloting", "replacement": "بیلٹنگ"},
    {"phrase": "supervisor", "replacement": "سپروائزر"},
    {"phrase": "representative", "replacement": "ریپریزنٹیٹو"},
    {"phrase": "callback", "replacement": "کال بیک"},
    {"phrase": "online", "replacement": "آن لائن"},
    {"phrase": "account", "replacement": "اکاؤنٹ"},
    {"phrase": "delivery", "replacement": "ڈیلیوری"},
    {"phrase": "discount", "replacement": "ڈسکاؤنٹ"},
    {"phrase": "team", "replacement": "ٹیم"},
]

async def setup_phrase_config(api_key: str) -> Optional[str]:
    """Find or create a phrase replacement config. Reuses existing to avoid 10-config limit."""
    for attempt in range(3):
        try:
            return await _setup_phrase_config_once(api_key)
        except Exception as e:
            if attempt < 2:
                log.warning(f"Phrase config attempt {attempt + 1} failed: {e} — retrying")
                await asyncio.sleep(1)
            else:
                log.warning(f"Phrase config failed after 3 attempts: {e}")

async def _setup_phrase_config_once(api_key: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(PHRASE_REPLACEMENT_URL, headers=headers)
        r.raise_for_status()
        configs = r.json()
        if configs:
            existing = configs[0]
            config_id = existing["configId"]
            r2 = await client.post(
                f"{PHRASE_REPLACEMENT_URL}/{config_id}",
                json={"phraseReplacements": _PHRASE_REPLACEMENTS},
                headers=headers,
            )
            r2.raise_for_status()
            log.info(f"Phrase replacement config updated: {config_id}")
            return config_id
        r2 = await client.post(
            PHRASE_REPLACEMENT_URL,
            json={"phraseReplacements": _PHRASE_REPLACEMENTS},
            headers=headers,
        )
        r2.raise_for_status()
        data = r2.json()
        config_id = data.get("configId")
        log.info(f"Phrase replacement config created: {config_id}")
        return config_id


class SaimaEngine(AgentEngine):
    AGENT_NAME = "Saima"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    DEEPGRAM_STT_LANGUAGE = "ur"
    BARGE_IN_WORD_COUNT = 3
    FILLER_PHRASES = _FILLERS_UR
    RETURN_ON_BARGE_IN = True
    HOLD_MUSIC_PATH = cfg.hold_music_path
    FALLBACK_TECHNICAL = "معذرت، تکنیکی مسئلہ آ رہا ہے۔ کیا آپ دوبارہ بتا سکتے ہیں؟"
    FALLBACK_NOT_UNDERSTOOD = "معذرت، آپ کی بات سمجھ نہیں آئی۔ دوبارہ بتائیں؟"

    def __init__(self, cfg: AgentConfig, reader, writer):
        super().__init__(cfg, reader, writer)
        self._initial_ntfy_sent = False
        self._complaint_created = False
        self._complaint_number: Optional[str] = None
        self._complaint_id: Optional[int] = None

    # ── TTS ────────────────────────────────────────────────────────────────

    async def text_to_speech(self, text: str) -> bytes:
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

    @classmethod
    async def _static_text_to_speech(cls, text: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = {
                "voiceId": cfg.uplifts_tts_voice_id,
                "text": text,
                "outputFormat": "WAV_22050_16",
            }
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

    # ── Override points ────────────────────────────────────────────────────

    def normalize_stt(self, text: str) -> str:
        return normalize_urdu_stt(text)

    def normalize_tts(self, text: str) -> str:
        return normalize_tts_text(text)

    def get_greeting(self) -> str:
        if self.caller_name:
            return (
                f"السلام علیکم {self.caller_name} صاحب! پنجاب سہولت بازار اتھارٹی میں دوبارہ خوش آمدید۔ "
                f"میں سائمہ ہوں۔ آج میں آپ کی کیا مدد کر سکتی ہوں؟"
            )
        return (
            "السلام علیکم! میں سائمہ بول رہی ہوں PSBA ہیلپ لائن سے۔ "
            "آپ کی کیا مدد کر سکتی ہوں؟"
        )

    def get_farewell_extra(self) -> str:
        return "کیا میں آپ کی اور کوئی مدد کر سکتی ہوں؟"

    def get_transfer_fallback(self) -> str:
        return (
            "معذرت، ابھی سب lines busy ہیں — "
            "آپ کا نام اور نمبر note کر لیتی ہوں، senior representative جلد از جلد call کریں گے۔ "
            "نام بتائیں؟"
        )

    # ── Call setup: Chatwoot + Odoo lookup ─────────────────────────────────

    async def _on_call_setup(self):
        if not self.caller_phone:
            return
        try:
            crm = await asyncio.wait_for(chatwoot_lookup(self.caller_phone, self.cfg), timeout=5)
        except asyncio.TimeoutError:
            crm = {}
            log.warning(f"[{self.call_id}] Chatwoot lookup timed out")
        if crm.get("history"):
            self.caller_name = crm.get("name", "")
            self.caller_context = (
                f"## واپس آنے والے کلائنٹ\n"
                f"نام: {crm['name']}\n"
                f"فون: {self.caller_phone}\n"
                f"پچھلی کالز: {crm['total_calls']}\n\n"
                f"پچھلی گفتگو کی تاریخ (تازہ ترین پہلے):\n"
                f"{crm['history']}\n\n"
                f"اس معلومات کو استعمال کرکے گفتگو کو ذاتی بنائیں۔ "
                f"انہیں نام سے مخاطب کریں۔ پچھلی دلچسپیوں کا قدرتی طریقے سے ذکر کریں۔ "
                f"جو معلومات پہلے سے موجود ہے وہ دوبارہ نہ پوچھیں۔"
            )
            log.info(f"[{self.call_id}] Returning caller: {crm['name']} — {crm['total_calls']} previous calls")
        else:
            log.info(f"[{self.call_id}] New caller: {self.caller_phone}")

        if self.odoo:
            try:
                partner = await self.odoo.search_partner(self.caller_phone)
                if partner:
                    log.info(f"[{self.call_id}] Odoo partner: {partner.get('name')} (id={partner.get('partner_id')})")
            except Exception as e:
                log.warning(f"[{self.call_id}] Odoo partner lookup failed: {e}")
            try:
                tickets = await self.odoo.search_tickets_by_phone(self.caller_phone, self.caller_name)
                if tickets:
                    lines = [
                        f"  - {t['number'] or '#' + str(t['id'])}: {t['name']} | Stage: {t['stage']} | Priority: {t['priority']}"
                        + (f" | Assigned: {t['assigned_to']}" if t['assigned_to'] else "")
                        for t in tickets
                    ]
                    ticket_block = (
                        f"\n\n## Caller ke open helpdesk tickets\n"
                        f"Caller {self.caller_phone} ke yeh open tickets hain:\n"
                        + "\n".join(lines) +
                        f"\n\nAgar caller apne ticket ke baare mein poochhe to yeh information use karein. "
                        f"Stage bata dein aur reassure karein ke team kaam kar rahi hai."
                    )
                    self.caller_context += ticket_block
                    log.info(f"[{self.call_id}] Found {len(tickets)} open tickets")
            except Exception as e:
                log.warning(f"[{self.call_id}] Odoo ticket lookup failed: {e}")

    # ── Pre-LLM: background tasks ─────────────────────────────────────────

    async def on_before_llm(self, text: str) -> list:
        return [
            self._capture_name_phone(text),
            self._detect_gender(text),
        ]

    # ── Post-LLM: initial ntfy + complaint creation ───────────────────────

    async def on_after_llm(self, text: str, reply: str, action: Optional[str] = None) -> str:
        if not self._initial_ntfy_sent:
            asyncio.create_task(self._send_initial_ntfy(text))
        if not self._complaint_created and self.odoo and action != self.cfg.ext_supervisor:
            complaint_keywords = ["کمپلینٹ", "کمپلينٹ", "رجسٹر", "note کر", "نوٹ کر", "شکایت"]
            if any(kw in reply for kw in complaint_keywords):
                result = await self.odoo.quick_create_ticket(
                    self.call_id, self.AGENT_NAME,
                    self.caller_phone or "",
                    self.caller_name or "",
                    reply[:120]
                )
                if result:
                    self._complaint_number = result["number"]
                    self._complaint_id = result["id"]
                    self._complaint_created = True
                    reply += f"\n\nآپ کی کمپلینٹ نمبر {result['number']} رجسٹر ہو گئی ہے۔ یہ نمبر note کر لیں، مستقبل میں اس سے اپنی کمپلینٹ کا status check کر سکیں گے۔"
        return reply

    # ── Gender detection ──────────────────────────────────────────────────

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

    # ── Initial ntfy notification ─────────────────────────────────────────

    async def _send_initial_ntfy(self, first_inquiry: str = ""):
        if self._initial_ntfy_sent:
            return
        self._initial_ntfy_sent = True
        topic = cfg.ntfy_topic
        server = cfg.ntfy_server
        if not topic:
            return
        try:
            body = (
                f"New incoming call\n"
                f"Phone: {self.caller_phone or 'unknown'}\n"
                f"Name: {self.caller_name or 'not yet captured'}\n"
                f"Inquiry: {first_inquiry}\n"
                f"Call ID: {self.call_id}"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{server}/{topic}",
                    content=body.encode("utf-8"),
                    headers={
                        "Title": f"Call in progress - {self.AGENT_NAME}",
                        "Priority": "default",
                        "Tags": "telephone",
                    }
                )
            log.info(f"[{self.call_id}] Initial ntfy sent")
        except Exception as e:
            log.warning(f"[{self.call_id}] Initial ntfy failed: {e}")

    # ── Farewell check (Urdu) ─────────────────────────────────────────────

    async def _check_farewell(self, reply: str, lang: str = "ur"):
        await super()._check_farewell(reply, lang)

    # ── Post-call pipeline ─────────────────────────────────────────────────

    async def post_call_actions(self, conversation: list, call_id: str, caller_phone: str = "", complaint_id: Optional[int] = None) -> None:
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
                await create_chatwoot_lead(brief_lead, conversation, call_id, self.AGENT_NAME, self.cfg)
            return

        lead = await extract_lead_data(conversation, self.AGENT_NAME, self.cfg)
        if not lead:
            log.warning(f"[{call_id}] Lead extraction returned empty")
            return

        if not lead.get('phone') and caller_phone:
            lead['phone'] = caller_phone

        log.info(f"[{call_id}] Lead: {lead.get('name')} | {lead.get('inquiry_type')} | score {lead.get('lead_temperature')}")

        async def odoo_task():
            if self.odoo:
                try:
                    if complaint_id:
                        await self.odoo.update_helpdesk_ticket(complaint_id, lead, conversation, call_id, self.AGENT_NAME)
                    else:
                        await self.odoo.create_helpdesk_ticket(lead, conversation, call_id, self.AGENT_NAME)
                except Exception as e:
                    log.warning(f"[{call_id}] Odoo helpdesk ticket operation failed: {e}")

        await asyncio.gather(
            send_ntfy_notification(lead, call_id, self.AGENT_NAME, self.cfg),
            create_chatwoot_lead(lead, conversation, call_id, self.AGENT_NAME, self.cfg),
            odoo_task(),
            send_gmail_notification(lead, conversation, call_id, self.AGENT_NAME, self.cfg),
            book_sales_appointment(lead, call_id, self.AGENT_NAME, self.cfg),
        )


# ── Server entry point ────────────────────────────────────────────────────────
async def handle_connection(reader, writer):
    addr = writer.get_extra_info("peername")
    log.info(f"Connection from {addr}")
    handler = SaimaEngine(cfg, reader, writer)
    await handler.run()

async def main():
    global _phrase_config_id
    SaimaEngine.setup_services(cfg)
    await SaimaEngine.ami.connect()
    await SaimaEngine.pregenerate_fillers()
    _phrase_config_id = await setup_phrase_config(cfg.uplifts_tts_api_key)
    log.info("=" * 60)
    log.info(f"PSBA — {SaimaEngine.AGENT_NAME} (Urdu, ext 8000)")
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
