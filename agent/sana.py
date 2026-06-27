#!/usr/bin/env python3
"""
AI Voice Sales Agent — Sana (Urdu Sales, ext 8500)
- AudioSocket: bridges Asterisk audio
- Deepgram Nova-3: real-time STT (ur)
- OpenAI GPT-4o-mini: cloud LLM
- Uplift TTS: Urdu TTS
"""

import asyncio
import logging
from typing import Optional

import httpx

from agent_lib import (
    load_env, load_sana_config, setup_log,
    normalize_tts_text,
    chatwoot_lookup, create_chatwoot_lead,
    send_ntfy_notification, send_gmail_notification, book_sales_appointment,
    extract_lead_data,
)
from agent_lib.engine import AgentEngine
from agent_lib.config import AgentConfig
from agent_lib.prompt_builder import build_agent_prompt

load_env()
cfg: AgentConfig = load_sana_config()

log = setup_log(__name__)

_FILLERS_UR: list[str] = []

SYSTEM_PROMPT = build_agent_prompt(cfg, "ur")

UPLIFT_TTS_REST_URL = "https://api.upliftai.org/v1/synthesis/text-to-speech"
PHRASE_REPLACEMENT_URL = "https://api.upliftai.org/v1/synthesis/phrase-replacement-config"

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

_phrase_config_id: Optional[str] = None

_PHRASE_REPLACEMENTS = [
    {"phrase": "PSBA", "replacement": "پی ایس بی اے"},
    {"phrase": "Sahulat Bazaar", "replacement": "سہولت بازار"},
    {"phrase": "سہولت بازار", "replacement": "سہولت بازار"},
    {"phrase": "Zoya", "replacement": "زویا"},
    {"phrase": "Sana", "replacement": "ثنا"},
    {"phrase": "helpline", "replacement": "ہیلپ لائن"},
    {"phrase": "AI", "replacement": "اے آئی"},
    {"phrase": "Lahore", "replacement": "لاہور"},
    {"phrase": "Punjab", "replacement": "پنجاب"},
    {"phrase": "app", "replacement": "ایپ"},
    {"phrase": "Play Store", "replacement": "پلے سٹور"},
    {"phrase": "App Store", "replacement": "ایپ سٹور"},
    {"phrase": "delivery", "replacement": "ڈیلیوری"},
    {"phrase": "discount", "replacement": "ڈسکاؤنٹ"},
    {"phrase": "stall", "replacement": "سٹال"},
    {"phrase": "balloting", "replacement": "بیلٹنگ"},
    {"phrase": "supervisor", "replacement": "سپروائزر"},
    {"phrase": "representative", "replacement": "ریپریزنٹیٹو"},
    {"phrase": "callback", "replacement": "کال بیک"},
    {"phrase": "online", "replacement": "آن لائن"},
    {"phrase": "account", "replacement": "اکاؤنٹ"},
    {"phrase": "team", "replacement": "ٹیم"},
    {"phrase": "payment", "replacement": "پیمنٹ"},
    {"phrase": "installment", "replacement": "قسط"},
]

async def setup_phrase_config(api_key: str) -> Optional[str]:
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


class SanaEngine(AgentEngine):
    AGENT_NAME = "Sana"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    DEEPGRAM_STT_LANGUAGE = "ur"
    BARGE_IN_WORD_COUNT = 3
    FILLER_PHRASES = _FILLERS_UR
    RETURN_ON_BARGE_IN = True
    HOLD_MUSIC_PATH = cfg.hold_music_path
    FALLBACK_TECHNICAL = "معذرت، تکنیکی مسئلہ آ رہا ہے۔ کیا آپ دوبارہ بتا سکتے ہیں؟"
    FALLBACK_NOT_UNDERSTOOD = "معذرت، آپ کی بات سمجھ نہیں آئی۔ دوبارہ بتائیں؟"

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

    def normalize_stt(self, text: str) -> str:
        return normalize_urdu_stt(text)

    def normalize_tts(self, text: str) -> str:
        return normalize_tts_text(text)

    def get_greeting(self) -> str:
        if self.caller_name:
            return (
                f"السلام علیکم {self.caller_name} صاحب! {COMPANY_NAME} میں خوش آمدید۔ "
                f"میں ثنا ہوں۔ بتائیں میں آپ کی کیا مدد کر سکتی ہوں؟"
            )
        return (
            "السلام علیکم! میں ثنا بول رہی ہوں — بتائیں آپ کس چیز کے بارے میں معلومات لینا چاہیں گے؟"
        )

    def get_farewell_extra(self) -> str:
        return "کیا میں آپ کی اور کوئی مدد کر سکتی ہوں؟"

    def get_transfer_fallback(self) -> str:
        return (
            "معذرت، ابھی سب lines busy ہیں — "
            "آپ کا نام اور نمبر note کر لیتی ہوں، senior representative جلد از جلد call کریں گے۔ "
            "نام بتائیں؟"
        )

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

    async def on_before_llm(self, text: str) -> list:
        return [self._capture_name_phone(text)]

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
                    await self.odoo.create_lead(lead, conversation, call_id, self.AGENT_NAME)
                except Exception as e:
                    log.warning(f"[{call_id}] Odoo lead creation failed: {e}")

        await asyncio.gather(
            send_ntfy_notification(lead, call_id, self.AGENT_NAME, self.cfg),
            create_chatwoot_lead(lead, conversation, call_id, self.AGENT_NAME, self.cfg),
            odoo_task(),
            send_gmail_notification(lead, conversation, call_id, self.AGENT_NAME, self.cfg),
            book_sales_appointment(lead, call_id, self.AGENT_NAME, self.cfg),
        )


async def handle_connection(reader, writer):
    addr = writer.get_extra_info("peername")
    log.info(f"Connection from {addr}")
    handler = SanaEngine(cfg, reader, writer)
    await handler.run()

async def main():
    global _phrase_config_id
    SanaEngine.setup_services(cfg)
    await SanaEngine.ami.connect()
    await SanaEngine.pregenerate_fillers()
    _phrase_config_id = await setup_phrase_config(cfg.uplifts_tts_api_key)
    log.info("=" * 60)
    log.info(f"PSBA — {SanaEngine.AGENT_NAME} (Urdu Sales, ext 8500)")
    log.info(f"  AudioSocket : {cfg.ami_host}:{cfg.audiosocket_port}")
    log.info(f"  LLM         : OpenAI {cfg.openai_model}")
    log.info(f"  STT         : Deepgram Nova-3 ur (streaming)")
    log.info(f"  TTS         : Uplift REST voice={cfg.uplifts_tts_voice_id}")
    log.info(f"  Phrase cfg  : {_phrase_config_id or 'none'}")
    log.info(f"  Prompt size : {len(SYSTEM_PROMPT):,} chars")
    log.info("=" * 60)

    server = await asyncio.start_server(
        handle_connection, cfg.ami_host, cfg.audiosocket_port
    )
    log.info(f"Listening on port {cfg.audiosocket_port} — dial 8500 to talk to Sana")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
