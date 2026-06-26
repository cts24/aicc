#!/usr/bin/env python3
"""
PSBA AI Voice Agent — Sara (English, ext 9000)
- AudioSocket: bridges Asterisk audio
- Deepgram Nova-2: real-time STT via WebSocket
- OpenAI GPT-4o-mini: cloud LLM
- Deepgram Aura: TTS
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from agent_lib import (
    load_env, load_sara_config, setup_log,
    chatwoot_lookup, create_chatwoot_lead,
    send_ntfy_notification, send_gmail_notification, book_sales_appointment,
    extract_lead_data,
)
from agent_lib.engine import AgentEngine
from agent_lib.config import AgentConfig

load_env()
cfg: AgentConfig = load_sara_config()

log = setup_log(__name__)

DEEPGRAM_TTS_URL = (
    "https://api.deepgram.com/v1/speak"
    "?model=aura-asteria-en"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&container=none"
)

# ── Filler phrases (English) ─────────────────────────────────────────────────
_FILLERS_EN = ["Sure!", "Of course!", "Let me check.", "Absolutely!", "Happy to help!"]

# ── Load Knowledge Base ───────────────────────────────────────────────────────
KNOWLEDGE_BASE = cfg.kb_path.read_text() if cfg.kb_path.exists() else ""

# ── Load system prompt from file ─────────────────────────────────────────────
_SARA_PROMPT_PATH = Path(__file__).parent / "sara_prompt.txt"
_SARA_PROMPT_TEMPLATE = _SARA_PROMPT_PATH.read_text(encoding="utf-8") if _SARA_PROMPT_PATH.exists() else ""
SYSTEM_PROMPT = _SARA_PROMPT_TEMPLATE.replace("{KNOWLEDGE_BASE}", KNOWLEDGE_BASE)


class SaraEngine(AgentEngine):
    AGENT_NAME = "Sara"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    DEEPGRAM_STT_LANGUAGE = "en-US"
    DEEPGRAM_STT_MODEL = "nova-2"
    BARGE_IN_WORD_COUNT = 4
    FILLER_PHRASES = _FILLERS_EN
    RETURN_ON_BARGE_IN = False
    HOLD_MUSIC_PATH = cfg.hold_music_path

    # ── TTS (Deepgram Aura) ────────────────────────────────────────────────

    async def text_to_speech(self, text: str) -> bytes:
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

    @classmethod
    async def _static_text_to_speech(cls, text: str) -> bytes:
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
                await asyncio.sleep(0.5)

    # ── Override points ────────────────────────────────────────────────────

    def get_greeting(self) -> str:
        if self.caller_name:
            return f"Welcome back, {self.caller_name}! How can I help you today?"
        return "Hello! This is Sara from PSBA — Punjab Sahulaat Bazaars Authority. How can I help you today?"

    def get_farewell_extra(self) -> str:
        return "Is there anything else I can help you with today?"

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
                f"## RETURNING CLIENT\n"
                f"Name: {crm['name']}\n"
                f"Phone: {self.caller_phone}\n"
                f"Previous Calls: {crm['total_calls']}\n\n"
                f"Recent Conversation History (newest first):\n"
                f"{crm['history']}\n\n"
                f"Use this information to personalise the conversation. "
                f"Address them by name. Mention their previous interests naturally. "
                f"Do not ask for information already provided."
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
                log.warning(f"[{self.call_id}] Odoo lookup failed: {e}")

    # ── Pre-LLM: background name/phone capture ────────────────────────────

    async def on_before_llm(self, text: str) -> list:
        return [self._capture_name_phone(text)]

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


# ── Server entry point ────────────────────────────────────────────────────────
async def handle_connection(reader, writer):
    addr = writer.get_extra_info("peername")
    log.info(f"Connection from {addr}")
    handler = SaraEngine(cfg, reader, writer)
    await handler.run()

async def main():
    SaraEngine.setup_services(cfg)
    await SaraEngine.ami.connect()
    await SaraEngine.pregenerate_fillers()
    log.info("=" * 60)
    log.info(f"PSBA — {SaraEngine.AGENT_NAME} (English, ext 9000)")
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
