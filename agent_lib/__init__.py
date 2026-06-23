"""agent_lib — shared library for PSBA AI Voice Call Center agents."""

from .config import AgentConfig, load_env, load_saima_config, load_sara_config, load_zara_config
from .log import setup_log
from .audiosocket import AS_HANGUP, AS_UUID, AS_AUDIO, AS_AUDIO_SLIN16, AS_ERROR, pack_frame, read_frame, downsample_16k_to_8k
from .phone import normalize_phone
from .speech import is_farewell_response, strip_gap_words, urdu_phonetic
from .ami import AMIClient, get_caller_id
from .chatwoot import chatwoot_lookup, create_chatwoot_lead
from .ntfy import send_ntfy_notification
from .gmail import send_gmail_notification
from .calendar import book_sales_appointment
from .odoo import OdooClient
from .llm import llm_respond, extract_name_phone, extract_lead_data, parse_transfer_tag
