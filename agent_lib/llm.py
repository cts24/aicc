"""LLM interaction — respond, extract, detect."""
import asyncio
import json
import logging
import re
from typing import Optional
import httpx

from .config import AgentConfig

log = logging.getLogger(__name__)


MAX_CONV_HISTORY = 10

TRANSFER_TAGS_CACHE: dict[str, str] = {}


def parse_transfer_tag(text: str, ext_supervisor: str = "3000") -> tuple:
    tags = {"[TRANSFER:SUPERVISOR]": ext_supervisor}
    for tag, ext in tags.items():
        if tag in text:
            return text.replace(tag, "").strip(), ext
    return text, None


async def llm_respond(
    conversation: list,
    system_prompt: str,
    cfg: AgentConfig,
    caller_context: str = "",
) -> str:
    system = system_prompt + (f"\n\n{caller_context}" if caller_context else "")
    trimmed = conversation[-MAX_CONV_HISTORY:] if len(conversation) > MAX_CONV_HISTORY else conversation
    messages = [{"role": "system", "content": system}] + trimmed
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(cfg.openai_url, json={
                    "model": cfg.openai_model,
                    "messages": messages,
                    "max_tokens": cfg.openai_max_tokens,
                    "temperature": cfg.openai_temperature,
                }, headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == 2:
                raise
            wait = 5 if "429" in str(e) else 0.5
            log.warning(f"LLM attempt {attempt + 1} failed: {e} — retrying in {wait}s")
            await asyncio.sleep(wait)


async def extract_name_phone(text: str, cfg: AgentConfig) -> tuple:
    prompt = (
        "Extract the caller's name and Pakistani phone number from this speech.\n"
        "Return ONLY valid JSON: {\"name\": \"...\", \"phone\": \"...\"}\n"
        "Rules:\n"
        "- name: first/full name only in Urdu (Arabic) script or English. null if not found.\n"
        "- phone: digits only, no spaces/dashes, 10 or 11 digits. null if incomplete.\n"
        f"Text: {text}"
    )
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(cfg.openai_url, json={
                "model": cfg.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 40,
                "temperature": 0,
            }, headers={"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"})
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"].strip())
            return data.get("name") or None, data.get("phone") or None
    except Exception:
        return None, None


async def extract_lead_data(conversation: list, agent_name: str, cfg: AgentConfig) -> dict:
    if len(conversation) < 2:
        return {}
    transcript = "\n".join([
        f"{'Caller' if m['role'] == 'user' else agent_name}: {m['content']}"
        for m in conversation
    ])
    prompt = f"""Extract lead info from this PSBA customer service call. Return ONLY valid JSON, nothing else.

Transcript:
{transcript}

Return this exact JSON (use null for missing):
{{
  "name": "caller name or null",
  "phone": "phone number or null",
  "email": "email or null",
  "inquiry_type": "bazaar location/products/prices/app help/stall inquiry/vendor registration/complaint/general info or null",
  "stall_interest": true or false or null,
  "complaint_details": "brief complaint description or null",
  "lead_temperature": <1-10 interest score>,
  "outcome": "interested/not_interested/needs_followup/resolved",
  "summary": "2-sentence call summary"
}}"""
    llm_url = cfg.groq_url or cfg.openai_url
    llm_key = cfg.groq_api_key or cfg.openai_api_key
    llm_model = cfg.groq_model or cfg.openai_model
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(llm_url, json={
                "model": llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": cfg.groq_max_tokens,
                "temperature": 0.1,
            }, headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
                if match:
                    return json.loads(match.group())
    except Exception as e:
        log.warning(f"Lead extraction failed: {e}")
    return {}
