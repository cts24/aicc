"""Load config from .env with per-agent prefix support."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ENV_PATH = Path("/opt/aiagent/.env")
LOCAL_ENV = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if not os.environ.get(key):
            os.environ[key] = val


def load_env() -> None:
    _load_dotenv(ENV_PATH)
    _load_dotenv(LOCAL_ENV)


def _e(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _ei(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


@dataclass
class AgentConfig:
    agent_name: str
    audiosocket_port: int
    kb_path: Path = Path("/opt/aiagent/prompts/knowledge_base.txt")

    # ── Product identity (per client override) ────────────────────────────
    company_name: str = ""
    helpline: str = ""
    agent_type: str = ""          # urdu_agent | english_agent | receptionist
    industry: str = "general"     # general | public_sector | real_estate | healthcare | etc.

    deepgram_api_key: str = ""
    openai_api_key: str = ""
    openai_url: str = "https://api.openai.com/v1/chat/completions"
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 150
    openai_temperature: float = 0.65

    groq_api_key: str = ""
    groq_url: str = ""
    groq_model: str = ""
    groq_max_tokens: int = 300

    uplifts_tts_api_key: str = ""
    uplifts_tts_voice_id: str = ""
    uplift_tts_model: str = ""

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = ""

    ami_host: str = "127.0.0.1"
    ami_port: int = 5038
    ami_user: str = ""
    ami_secret: str = ""
    ami_callerid_var: str = ""
    ami_channel_port: int = 0

    transfer_context: str = "from-internal"
    ext_supervisor: str = "3000"

    ntfy_topic: str = ""
    ntfy_server: str = ""

    chatwoot_url: str = ""
    chatwoot_token: str = ""
    chatwoot_account: int = 1
    chatwoot_inbox: int = 1

    gmail_sender: str = ""
    gmail_password: str = ""
    gmail_to: str = ""

    google_service_account_file: str = "/opt/aiagent/google_service_account.json"
    google_calendar_id: str = ""
    appointment_duration_minutes: int = 30

    odoo_url: str = "https://odoo.44-194-44-98.sslip.io"
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_password: str = ""

    hold_music_path: str = "/opt/aiagent/sounds/hold_music.raw"
    kb_path_str: str = ""


def load_saima_config() -> AgentConfig:
    return AgentConfig(
        agent_name=_e("SAIMA_AGENT_NAME", "Saima"),
        company_name=_e("COMPANY_NAME", "PSBA"),
        helpline=_e("HELPLINE", "0307-0002345"),
        agent_type="urdu_agent",
        industry=_e("INDUSTRY", "public_sector"),
        audiosocket_port=_ei("SAIMA_AUDIOSOCKET_PORT", 9094),
        deepgram_api_key=_e("DEEPGRAM_API_KEY"),
        openai_api_key=_e("OPENAI_API_KEY"),
        openai_model=_e("OPENAI_MODEL", "gpt-4o-mini"),
        openai_max_tokens=_ei("SAIMA_OPENAI_MAX_TOKENS", 150),
        groq_api_key=_e("GROQ_API_KEY"),
        groq_url=_e("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
        groq_model=_e("SAIMA_GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        uplifts_tts_api_key=_e("UPLIFT_TTS_API_KEY"),
        uplifts_tts_voice_id=_e("UPLIFT_TTS_VOICE_ID", "helpdesk-agent"),
        uplift_tts_model=_e("UPLIFT_TTS_MODEL", "v_meklc281"),
        ami_user=_e("SAIMA_AMI_USER", "saima"),
        ami_secret=_e("SAIMA_AMI_SECRET", "SaimaAMI2025"),
        ami_callerid_var=_e("SAIMA_AMI_CALLERID_VAR", "SAIMA_CALLERID"),
        ami_channel_port=_ei("SAIMA_AUDIOSOCKET_PORT", 9094),
        ntfy_topic=_e("NTFY_LEADS_TOPIC", "psba_leads"),
        ntfy_server=_e("NTFY_SERVER", "http://44.194.44.98:8090"),
        chatwoot_url=_e("CHATWOOT_URL", "http://44.194.44.98:3000"),
        chatwoot_token=_e("CHATWOOT_TOKEN"),
        chatwoot_account=_ei("CHATWOOT_ACCOUNT_ID", 1),
        chatwoot_inbox=_ei("CHATWOOT_INBOX_ID", 1),
        gmail_sender=_e("GMAIL_SENDER"),
        gmail_password=_e("GMAIL_PASSWORD"),
        gmail_to=_e("GMAIL_TO"),
        google_calendar_id=_e("GOOGLE_CALENDAR_ID"),
        odoo_url=_e("ODOO_URL", "https://odoo.44-194-44-98.sslip.io"),
        odoo_db=_e("ODOO_DB", "odoo"),
        odoo_username=_e("ODOO_USERNAME", "admin"),
        odoo_password=_e("ODOO_PASSWORD", "admin"),
        kb_path=Path(_e("KB_PATH", "/opt/aiagent/prompts/knowledge_base.txt")),
    )


def load_sara_config() -> AgentConfig:
    return AgentConfig(
        agent_name=_e("SARA_AGENT_NAME", "Sara"),
        company_name=_e("COMPANY_NAME", "PSBA"),
        helpline=_e("HELPLINE", "0307-0002345"),
        agent_type="english_agent",
        industry=_e("INDUSTRY", "public_sector"),
        audiosocket_port=_ei("SARA_AUDIOSOCKET_PORT", 9092),
        deepgram_api_key=_e("DEEPGRAM_API_KEY"),
        openai_api_key=_e("OPENAI_API_KEY"),
        openai_model=_e("OPENAI_MODEL", "gpt-4o-mini"),
        openai_max_tokens=_ei("SARA_OPENAI_MAX_TOKENS", 100),
        groq_api_key=_e("GROQ_API_KEY"),
        groq_url=_e("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
        groq_model=_e("SARA_GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        ami_user=_e("SARA_AMI_USER", "sara"),
        ami_secret=_e("SARA_AMI_SECRET", "SaraAMI2025"),
        ami_callerid_var=_e("SARA_AMI_CALLERID_VAR", "SARA_CALLERID"),
        ami_channel_port=_ei("SARA_AUDIOSOCKET_PORT", 9092),
        ntfy_topic=_e("NTFY_LEADS_TOPIC", "psba_leads"),
        ntfy_server=_e("NTFY_SERVER", "http://44.194.44.98:8090"),
        chatwoot_url=_e("CHATWOOT_URL", "http://44.194.44.98:3000"),
        chatwoot_token=_e("CHATWOOT_TOKEN"),
        chatwoot_account=_ei("CHATWOOT_ACCOUNT_ID", 1),
        chatwoot_inbox=_ei("CHATWOOT_INBOX_ID", 1),
        gmail_sender=_e("GMAIL_SENDER"),
        gmail_password=_e("GMAIL_PASSWORD"),
        gmail_to=_e("GMAIL_TO"),
        google_calendar_id=_e("GOOGLE_CALENDAR_ID"),
        odoo_url=_e("ODOO_URL", "https://odoo.44-194-44-98.sslip.io"),
        odoo_db=_e("ODOO_DB", "odoo"),
        odoo_username=_e("ODOO_USERNAME", "admin"),
        odoo_password=_e("ODOO_PASSWORD", "admin"),
        kb_path=Path(_e("KB_PATH", "/opt/aiagent/prompts/knowledge_base.txt")),
    )


def load_zara_config() -> AgentConfig:
    return AgentConfig(
        agent_name=_e("ZARA_AGENT_NAME", "Zara"),
        company_name=_e("COMPANY_NAME", "PSBA"),
        helpline=_e("HELPLINE", "0307-0002345"),
        agent_type="receptionist",
        industry=_e("INDUSTRY", "public_sector"),
        audiosocket_port=_ei("ZARA_AUDIOSOCKET_PORT", 9096),
        deepgram_api_key=_e("DEEPGRAM_API_KEY"),
        openai_api_key=_e("OPENAI_API_KEY"),
        openai_model=_e("OPENAI_MODEL", "gpt-4o-mini"),
        openai_max_tokens=_ei("ZARA_OPENAI_MAX_TOKENS", 150),
        elevenlabs_api_key=_e("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_e("ELEVENLABS_VOICE_ID", "Ukfq9vQ0QNLZ4MGK0Uxc"),
        elevenlabs_model=_e("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        ami_user=_e("ZARA_AMI_USER", "zara"),
        ami_secret=_e("ZARA_AMI_SECRET", "ZaraAMI2025"),
        ami_callerid_var="ZARACHAN",
        ami_channel_port=_ei("ZARA_AUDIOSOCKET_PORT", 9096),
        ntfy_server=_e("NTFY_SERVER", "http://44.194.44.98:8090"),
        chatwoot_url=_e("CHATWOOT_URL", "http://44.194.44.98:3000"),
        chatwoot_token=_e("CHATWOOT_TOKEN"),
        google_calendar_id=_e("GOOGLE_CALENDAR_ID"),
        kb_path=Path(_e("KB_PATH", "/opt/aiagent/prompts/knowledge_base.txt")),
    )
