"""Build system prompts from layered components: core + expertise + context + KB."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Resolve base directory — works both locally (agent/ subdir) and on EC2 (flat layout)
_BASE = Path(__file__).resolve().parent.parent
_AGENT_DIR = _BASE / "agent" if (_BASE / "agent" / "core").is_dir() else _BASE


def build_agent_prompt(cfg, language: str = "en") -> str:
    """Assemble full system prompt from layered files.

    Layers:
      1. Core persona (our IP — same for all clients)
      2. Industry expertise (built once per industry)
      3. Client context (per-client identity + key facts)
      4. Knowledge base (per-client data)

    Placeholders in core persona files:
      {AGENT_NAME}     → cfg.agent_name
      {COMPANY_NAME}   → cfg.company_name
      {HELPLINE}       → cfg.helpline
      {CLIENT_CONTEXT} → prompts/client_context.txt
      {EXPERTISE}      → expertise/{industry}.txt
      {KNOWLEDGE_BASE} → prompts/knowledge_base.txt
    """
    # Layer 1: Core persona
    if language == "ur":
        core_name = f"{cfg.agent_type}_ur_persona.txt"
    else:
        core_name = f"{cfg.agent_type}_persona.txt"

    core_path = _AGENT_DIR / "core" / core_name
    if not core_path.exists():
        core_path = _AGENT_DIR / "core" / f"{cfg.agent_type}_persona.txt"
    if not core_path.exists():
        log.warning(f"Core persona not found: {core_path}")
        return ""

    try:
        core = core_path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to read core persona {core_path}: {e}")
        return ""

    # Layer 2: Industry expertise
    expertise = ""
    if cfg.industry:
        exp_path = _AGENT_DIR / "expertise" / f"{cfg.industry}.txt"
        if exp_path.exists():
            try:
                expertise = exp_path.read_text(encoding="utf-8")
            except Exception as e:
                log.warning(f"Failed to read expertise {exp_path}: {e}")

    # Layer 3: Client context
    context = ""
    context_path = _AGENT_DIR / "prompts" / "client_context.txt"
    if context_path.exists():
        try:
            context = context_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"Failed to read client context: {e}")

    # Layer 4: Knowledge base
    kb = ""
    kb_path = _AGENT_DIR / "prompts" / "knowledge_base.txt"
    if kb_path.exists():
        try:
            kb = kb_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"Failed to read knowledge base: {e}")

    # Assemble
    result = (core
        .replace("{AGENT_NAME}", cfg.agent_name or "Agent")
        .replace("{COMPANY_NAME}", cfg.company_name or "")
        .replace("{HELPLINE}", cfg.helpline or "")
        .replace("{CLIENT_CONTEXT}", context)
        .replace("{EXPERTISE}", expertise)
        .replace("{KNOWLEDGE_BASE}", kb))

    log.info(
        f"Prompt built: agent_type={cfg.agent_type} "
        f"industry={cfg.industry or 'none'} "
        f"lang={language} "
        f"core={len(core)}B expert={len(expertise)}B "
        f"ctx={len(context)}B kb={len(kb)}B "
        f"total={len(result)}B"
    )
    return result
