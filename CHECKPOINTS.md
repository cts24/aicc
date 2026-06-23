# Checkpoints — PSBA AI Voice Call Center

## 2026-06-23 — Saima Ugprade: Natural Urdu Prompt + Sleep Fix + Architecture Audit

### Changes Made

| # | Change | Files | Status |
|---|---|---|---|
| 1 | **Saima system prompt rewritten** — natural conversational Urdu style with code-switching examples matching Uplift helpdesk-agent voice | `agent/saima.py` (lines 87-164) | Deployed |
| 2 | **Sleep timing fixed** — all `asyncio.sleep(0.020)` → `0.018` across all 3 agents to rebuild jitter buffer | `agent/saima.py`, `agent/voice_agent.py`, `agent/zara.py` | Deployed |
| 3 | **CLAUDE.md fully updated** — verified architecture from live EC2, 16kHz pipeline, all services documented | `CLAUDE.md` | Updated |
| 4 | **CHECKPOINTS.md updated** — this checkpoint file | `CHECKPOINTS.md` | Updated |

### Root Cause: Voice Lag Jitter

- **sleep(0.020)** + write+drain (~1ms) = 21ms cycle for 20ms audio → buffer slowly drains → audible gaps
- **sleep(0.018)** gives 2ms slack per chunk (= 10% buffer), absorbing event loop jitter
- This was the original approach before the "fix" to 0.020 caused the problem

### Prompt Style: Uplift Natural Urdu

The helpdesk-agent sample text from Uplift:
> "اکثر وہاں ایسی صورت میں کچھ کمپنسیشن کی گنجائش ہوتی ہے۔ میں آپ کے لیے ابھی ٹیمپریری ہولڈ لگا دیتی ہوں یعنی آپ کی سیٹ فوراً کینسل نہیں ہوگی۔"

Key characteristics applied to Saima's prompt:
- English loanwords used naturally (call, team, delivery, order, product, app)
- No "pure Urdu" enforcement — code-switching is natural
- Short, flowing sentences
- Warm feminine verbs: بتا رہی ہوں, کر سکتی ہوں, لیتی ہوں
- Sample dialogue in prompt shows the expected output style

### What's Still Broken

| Issue | Severity |
|---|---|
| SSL cert expired 2026-06-20 | HIGH — needs `sudo certbot renew` |
| Odoo XMLRPC: invalid XML chars in transcript → ExpatError | MEDIUM |
| Odoo route missing from nginx | MEDIUM |

---

## 2026-06-22 — Odoo 19 CE Installation & CRM Integration

### Changes Made

| # | Change | Files | Status |
|---|---|---|---|
| 1 | Installed Odoo 19 CE via Docker on EC2 | `/opt/odoo/docker-compose.yml` | Deployed |
| 2 | Created `agent_lib/odoo.py` — async XMLRPC client with `search_partner()` and `create_lead()` | `agent_lib/odoo.py` | Deployed |
| 3 | Added `OdooClient` to agent_lib `__init__.py` | `agent_lib/__init__.py` | Deployed |
| 4 | Integrated post-call pipeline: ntfy → Chatwoot → Odoo (parallel) | `agent/saima.py`, `agent/voice_agent.py` | Deployed |
| 5 | Added Odoo env vars to `.env` and `config.py` | `.env.example`, `agent_lib/config.py` | Deployed |

### Known Issues

- **Odoo XMLRPC fails** on transcripts with invalid XML characters → `ExpatError`
- **Odoo nginx route** not configured yet — no HTTPS access

---

## 2026-06-22 — G.722 Wideband + 16kHz Audio Pipeline

### Changes Made

| # | Change | Files | Status |
|---|---|---|---|
| 1 | Added `allow=g722` before ulaw in all 8 PJSIP endpoints | `/etc/asterisk/pjsip.conf` | Deployed |
| 2 | Added `AS_AUDIO_SLIN16 = 0x12` constant | `agent_lib/audiosocket.py` | Deployed |
| 3 | Changed all outgoing audio to use SLIN16 kind byte (was 0x10) | All 3 agents | Deployed |
| 4 | Added `downsample_16k_to_8k()` via decimation for STT | `agent_lib/audiosocket.py` | Deployed |
| 5 | Changed ffmpeg pipeline from `atempo=0.9` + 8kHz to `aresample=16000:resampler=soxr:precision=28` | All 3 agents | Deployed |
| 6 | Changed chunk size 320→640 in play_audio/silence loops | All 3 agents | Deployed |
| 7 | Hold music regenerated 128KB→256KB at 16kHz | `sounds/hold_music.raw` | Deployed |
| 8 | Changed sleep timing from `0.018` → `0.020` | All 3 agents | **REVERTED** 2026-06-23 |

---

## 2026-06-13 — Saima Urdu TTS Overhaul & Filler Fix

### Changes Made

| # | Change | Files | Status |
|---|---|---|---|
| 1 | Switched Saima TTS from ElevenLabs Sana → Uplift TTS helpdesk-agent | `agent/saima.py`, `.env`, `.env.example` | Deployed |
| 2 | Output format: MP3_22050_128 → WAV_22050_16 | `agent/saima.py` | Deployed |
| 3 | Added `strip_gap_words()` — code-level Urdu filler word removal | `agent_lib/speech.py` | Deployed |
| 4 | Emptied `_FILLERS_UR` — removed all pre-generated filler audio | `agent/saima.py` | Deployed |
| 5 | Extended phonetic normaliser for contact info | `agent_lib/speech.py` | Deployed |
| 6 | Digit splitting for phone numbers | `agent_lib/speech.py` | Deployed |

### Notes

- `strip_gap_words()` and `urdu_phonetic()` are still in `agent_lib/speech.py` but **NOT called** from Saima's `speak()` anymore (removed 2026-06-23). They're retained for Zara's Urdu pipeline.
