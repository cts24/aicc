# Saima Urdu Voice Agent — Complete Build Recipe

> **Purpose:** Rebuild this exact Urdu voice agent for any company.
> **How to use with opencode:** Place this file at `.opencode/skills/saima-voice-agent.md` in your project root. Then tell any opencode AI: *"Use the saima-voice-agent skill to build a new Urdu voice agent for [Company Name]."*
> The AI will load this file, extract the settings, and scaffold a new agent using the template below.
>
> Every value below is production-tuned. Change only the `[COMPANY]` placeholders.

---

## 1. Architecture Overview

```
Caller → Asterisk (PJSIP) → AudioSocket (SLIN16 16kHz) → Agent
                                                              │
                    ┌─────────────────────────────────────────┤
                    │              asyncio.gather(4)          │
                    ▼                                         ▼
           asterisk_reader() ──→ audio_queue ──→ dg_sender() ──→ Deepgram STT
                                                                    │
                                                              dg_receiver()
                                                                    │
                                                              UtteranceEnd
                                                                    │
                                                              handle_transcript()
                                                                    │
                                                              llm_respond()
                                                                    │
                                                              speak()
                                                                    │
                                                              play_audio()
                                                                    │
                                                    AudioSocket ← ─┘
```

**Critical pattern:** `asyncio.gather(asterisk_reader, dg_receiver, dg_sender, greeting_task)` — 4 concurrent coroutines. All share `stop_event` for clean shutdown.

---

## 2. Audio Pipeline Settings (THE GOLDEN VALUES)

### Frame format: AudioSocket SLIN16
```
Frame:  [type:1B][length:2B BE][data]
0x00 = HANGUP
0x01 = UUID
0x10 = AUDIO (8kHz SLIN — NOT USED)
0x12 = AUDIO_SLIN16 (16kHz PCM — USE THIS)
0xff = ERROR
```

### Chunk size & timing
```python
CHUNK_SIZE = 640          # bytes = 320 samples = 20ms at 16kHz
SAMPLE_RATE = 16000       # Hz
CHUNK_DURATION = 320 / 16000  # = 0.020s (20ms)
SLEEP_TIME = 0.018        # 18ms — CRITICAL: 2ms slack per chunk
```

**Why 0.018 and not 0.020:**
- `asyncio.sleep(0.020)` has ~1-5ms jitter from event loop overhead
- Write + drain adds ~1ms overhead
- Result with 0.020: cycle = 21-26ms per 20ms chunk → buffer slowly drains → audible lag
- `sleep(0.018)` gives 2ms slack → buffer builds ~400ms over 10s → absorbs jitter

### Silence frame during TTS wait
```python
silence = b'\x00' * 640   # 20ms of silence at 16kHz
# Sent repeatedly while waiting for TTS API response
# Same sleep(0.018) timing — keeps audio pipeline flowing
```

### Filler phrases during LLM thinking
```python
_FILLERS_UR = []          # EMPTY — no filler audio
```

**This is deliberate.** No "umm", no "let me check". The silence sounds like an agent thinking. The Uplift TTS voice has natural prosody at the voice level — it doesn't need pre-generated filler.

### Hold music
```python
# 8s PCM 16kHz, soxr-upsampled from original 8kHz
# Used for supervisor transfer
# Path: /opt/aiagent/sounds/hold_music.raw — 256,000 bytes
```

---

## 3. Deepgram STT Configuration

```python
DEEPGRAM_STT_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16"
    "&sample_rate=8000"         # 8kHz for STT (downsampled from 16kHz)
    "&channels=1"
    "&model=nova-3"             # Deepgram's latest, best Urdu
    "&language=ur"              # Urdu-specific model — CRITICAL
    "&punctuate=true"
    "&endpointing=300"          # 300ms endpointing
    "&utterance_end_ms=1000"    # 1s silence = end of turn
    "&interim_results=true"     # For barge-in detection
    "&vad_events=true"          # Voice activity events
)
```

**Downsampling before STT** (`downsample_16k_to_8k`):
```python
# Simple decimation: take every 2nd sample
samples = array.array('h')
samples.frombytes(pcm_bytes)
return samples[::2].tobytes()
```
- 16kHz AudioSocket → 8kHz for Deepgram (their STT works at 8kHz)
- Decimation is acceptable here (no aliasing for speech). No need for soxr.

### Barge-in threshold
```python
# In dg_receiver():
if text and self.speaking and not self.barge_in.is_set() and len(text.split()) >= 3:
    self.barge_in.set()
```
- 3+ words during speaking → interrupt
- Checked on interim results (not just finals) — instant
- `play_audio()` checks `barge_in.is_set()` every chunk (every 20ms)
- **Unlike Sara, Saima returns after barge-in** rather than falling through

---

## 4. LLM Configuration

```python
# Conversation LLM (latency-critical)
model = "gpt-4o-mini"      # Fast, cheap, good Urdu
temperature = 0.65          # Creative enough for natural speech, not random
max_tokens = 150            # 2-3 sentences max — enforces brevity

# Lead extraction LLM (not latency-critical, uses free tier)
model = "meta-llama/llama-4-scout-17b-16e-instruct"  # via Groq (free)
temperature = 0.1           # Deterministic extraction
max_tokens = 300
```

**Conversation history trimming:**
```python
MAX_CONV_HISTORY = 10  # Last 10 turns only
conversation[-MAX_CONV_HISTORY:]
```

**LLM retry:**
```python
3 attempts, backoff: 5s on 429, 0.5s on other errors
```

---

## 5. Uplift TTS Configuration

```python
url = "https://api.upliftai.org/v1/synthesis/text-to-speech"
voice_id = "helpdesk-agent"     # Natural Urdu neural voice
output_format = "WAV_22050_16"   # 22kHz WAV from API
```

### Resampling: 22kHz → 16kHz
```python
proc = await asyncio.create_subprocess_exec(
    "ffmpeg", "-i", "pipe:0",
    "-f", "s16le", "-ac", "1", "-ar", "16000",
    "-af", "aresample=16000:resampler=soxr:precision=28",
    "pipe:1",
)
```
- `soxr` = SoX resampler library (highest quality)
- `precision=28` = 28-bit precision (near-perfect)
- This preserves the natural voice quality during sample rate conversion

### Phrase replacement config (optional, currently returns 400)
```python
# POST /v1/synthesis/phrase-replacement-config
# Maps domain terms to proper Urdu pronunciation
# e.g., "PSBA" → "پی ایس بی اے", "Sahulat Bazaar" → "سہولت بازار"
```

---

## 6. Text Normalization Pipeline

### normalize_tts_text() — applied BEFORE TTS (saima.py speak path)

Order of operations:
1. **Strip markdown**: `**bold**` `*italic*` `# headings` `- lists` `| tables`
2. **Strip URLs**: Remove protocol, keep domain
3. **Email → phonetic**: `info@psba.gop.pk` → `info ایٹ psba ڈاٹ gop ڈاٹ pk`
4. **Phone numbers**: `0307-0002345` → digit-by-digit Urdu (`صفر تین صفر سات` ...)
5. **Long digits** (10+): Break into individual Urdu digit names
6. **Hyphenated digits**: `8-12` → `8 12`

### normalize_urdu_stt() — applied to STT output BEFORE LLM

Converts Urdu-script English loanwords → Roman so LLM understands them:
```python
_URDU_ENGLISH_MAP = {
    "کال": "call", "ایپ": "app", "ڈلیوری": "delivery",
    "ڈسکاؤن트": "discount", "کمپلینٹ": "complaint",
    "ٹرانسفر": "transfer", "مینیجر": "manager", ...
}
```

---

## 7. Call Handler State Machine (Minimal — by Design)

The agent has NO formal state machine. It uses boolean flags:

```python
self.thinking              = False  # LLM is processing
self.speaking              = False  # TTS playback in progress
self.barge_in              = asyncio.Event()
self.transfer_in_progress  = False
self.offered_goodbye       = False
```

**Transition rules (enforced in handle_transcript):**
- If `thinking` or `transfer_in_progress`: drop incoming transcript (return immediately)
- If `speaking` and text has ≥3 words: set `barge_in`, **return** (wait for next UtteranceEnd)
- Otherwise: set `thinking=True`, call LLM, speak response, set `thinking=False`

---

## 8. Post-Call Pipeline

```python
# Runs in background after call ends, via asyncio.create_task:
asyncio.gather(
    send_ntfy_notification(lead, call_id, agent_name, cfg),
    create_chatwoot_lead(lead, conversation, call_id, agent_name, cfg),
    odoo_create(),          # or update_helpdesk_ticket if complaint was created
    send_gmail_notification(lead, conversation, call_id, agent_name, cfg),
    book_sales_appointment(lead, call_id, agent_name, cfg),
)
```

**Flow:**
1. If < 2 turns AND phone known → Chatwoot `incomplete_call` record, return
2. If ≥ 2 turns → `extract_lead_data()` via Groq `llama-4-scout-17b`
3. Phone fallback: if Groq missed phone → use AMI caller ID
4. All 5 actions run in parallel via `asyncio.gather`

---

## 9. System Prompt — THE MASTER TEMPLATE

```python
SYSTEM_PROMPT = f"""You are {AGENT_NAME} — {COMPANY} ki Urdu customer service representative. Aapka kaam callers ki {COMPANY} se related madad karna hai. Aap warm, professional, aur natural {CITY} ki customer service agent hain.

## CRITICAL — NO MARKDOWN

TTS reads your output aloud. NEVER use: **bold**, *italic*, numbered lists, bullet points, # headings, tables, or | pipes. Speak in plain conversational Urdu sentences only. Output will be read by a voice synthesizer.

## Response length

Max 2-3 sentences per reply. Phone call, not written document.

## Domain guard

{COMPANY} ka kaam: {SERVICE_DESCRIPTION}.

Caller non-{COMPANY} baat kare ({OFF_TOPIC_EXAMPLES}):
"معذرت، یہ {COMPANY} helpline ہے۔ کیا میں آپ کی {COMPANY} سے متعلق کسی چیز میں مدد کر سکتی ہوں؟"

## Location answers — CONVERSATIONAL ONLY

WRONG: "1. Location A 2. Location B 3. Location C..." (never list)
RIGHT: "ہمارے {COUNT} مقامات ہیں۔ آپ کس علاقے میں ہیں؟ بتائیں تو قریب ترین بتا دوں۔"

If caller insists on full list: "{FULL_LOCATION_LIST} تفصیلی ایڈریس کے لیے {HELPLINE_NUMBER} پر کال کریں۔"

## Repeat prevention

If caller asks same question again after you answered: "جی، میں نے ابھی بتایا تھا — کچھ اور پوچھنا ہے؟"
If caller asks same question 3rd time: "لگتا ہے لائن میں مسئلہ ہے۔ براہ کرم {HELPLINE_NUMBER} پر کال کر کے دوبارہ بات کریں۔ شکریہ! اللہ حافظ۔"

## Transfer to supervisor

ONLY: caller asks supervisor, fraud confirmed, health injury, FIA/court/legal, can't calm
Tag at END: [TRANSFER:SUPERVISOR]
Fail: take name + number for callback

## Voice

- Natural Pakistani Urdu — English loanwords normal (call, team, delivery, app, complaint)
- Feminine verbs: بتا رہی ہوں, کر سکتی ہوں, لیتی ہوں
- Occasional backchannel ("جی"، "اچھا") when caller pauses — not after every sentence
- Occasional turn-yield ("ٹھیک ہے؟") — max 2-3 times per call
- 1-2 hesitations per call ("دیکھیں، اہ —") — sounds human
- Kabhi bhi "اور کچھ؟" mat bolein — "کچھ اور بتاؤں" zyada natural

## Caller gender

Caller_context specifies gender. If male: چاہتے, بتا رہے. If female: چاہتی, بتا رہی. Default masculine if unknown.

## TTS-friendly numbers

Phone: "صفر تین صفر سات۔ صفر صفر صفر دو تین چار پانچ"
Email: "انفو ایٹ کمپنی ڈاٹ کام"
Percentage: "{DISCOUNT} فیصد"
Price: "{PRICE_RANGE}"

## Key facts

Contact: {HELPLINE_NUMBER}, {PHONE_NUMBER}, {EMAIL}
App: {APP_INFO}
Stalls: {STALL_INFO}
Fraud: {COMPANY} never asks advance payment
Prices: {PRICING_INFO}

## Complaint handling (aap khud register karein, app/email pe na bhejein)
1. شکایت سنیں اور ہمدردی دکھائیں
2. تفصیلات پوچھیں — کیا، کب، کہاں
3. Confirm karein — "تو آپ کا مسئلہ یہ ہے کہ..."
4. Ticket register karein — caller ko ticket number batayein
5. Caller ka naam aur number lein

## Rules
- Kabhi specific price mat batao — board ya app check karein
- Non-{COMPANY} topics pe "معذرت، یہ {COMPANY} helpline ہے"
- {COMPANY} never asks payment — always fraud
- Agar caller same sawaal 3 baar poochhe — helpline number do aur call end karo
- Graceful exit: caller clearly done → farewell naturally, no extra question
- Farewell ONLY when caller initiates goodbye first. Don't say اللہ حافظ as routine.

{KNOWLEDGE_BASE}"""
```

---

## 10. Example .env Configuration

```ini
# ── Agent Identity ──
SAIMA_AUDIOSOCKET_PORT=9094
SAIMA_AMI_USER=saima
SAIMA_AMI_SECRET=YourAMI2025!
SAIMA_AMI_CALLERID_VAR=SAIMA_CALLERID
SAIMA_OPENAI_MAX_TOKENS=150
SAIMA_GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct

# ── STT ──
DEEPGRAM_API_KEY=dg_your_key_here

# ── LLM ──
OPENAI_API_KEY=sk-your_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_URL=https://api.openai.com/v1/chat/completions

# ── Extraction LLM (Groq = free) ──
GROQ_API_KEY=gsk_your_key_here
GROQ_URL=https://api.groq.com/openai/v1/chat/completions

# ── TTS ──
UPLIFT_TTS_API_KEY=your_uplift_key
UPLIFT_TTS_VOICE_ID=helpdesk-agent
UPLIFT_TTS_MODEL=v_meklc281

# ── Asterisk AMI ──
AMI_HOST=127.0.0.1
AMI_PORT=5038

# ── Routing ──
EXT_SUPERVISOR=3000
TRANSFER_CONTEXT=from-internal

# ── Chatwoot ──
CHATWOOT_URL=http://your-server:3000
CHATWOOT_TOKEN=your_chatwoot_token
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_INBOX_ID=1

# ── ntfy ──
NTFY_LEADS_TOPIC=your_company_leads
NTFY_SERVER=http://your-server:8090

# ── Odoo ──
ODOO_URL=http://localhost:8069
ODOO_DB=your_db
ODOO_USERNAME=admin
ODOO_PASSWORD=your_password

# ── Gmail (optional) ──
GMAIL_SENDER=you@gmail.com
GMAIL_PASSWORD=app_password
GMAIL_TO=reports@yourcompany.com

# ── Google Calendar (optional) ──
GOOGLE_CALENDAR_ID=calendar_id@group.calendar.google.com

# ── Paths ──
HOLD_MUSIC_PATH=/opt/aiagent/sounds/hold_music.raw
KB_PATH=/opt/aiagent/knowledge_base.txt
```

---

## 11. File Structure

```
your_project/
├── agent/
│   ├── saima.py           # Main agent (760 lines) — 1 file
│   ├── knowledge_base.txt # Company facts loaded into prompt
│   └── sounds/
│       └── hold_music.raw # 256KB PCM 16kHz hold music
├── agent_lib/
│   ├── __init__.py        # Re-exports all public API
│   ├── config.py          # AgentConfig dataclass + load_env()
│   ├── audiosocket.py     # pack_frame, read_frame, downsample_16k_to_8k
│   ├── speech.py          # normalize_tts_text, is_farewell_response, gender detect
│   ├── llm.py             # llm_respond, extract_lead_data, parse_transfer_tag
│   ├── ami.py             # AMIClient — Asterisk Manager Interface
│   ├── chatwoot.py        # Chatwoot CRM lookup + create
│   ├── odoo.py            # Odoo XMLRPC client
│   ├── ntfy.py            # ntfy push notifications
│   ├── gmail.py           # Gmail SMTP
│   ├── calendar.py        # Google Calendar booking
│   ├── phone.py           # normalize_phone()
│   └── log.py             # JSON-structured logging
├── .env                   # chmod 600, owned by service user
└── saima.service          # systemd unit
```

---

## 12. Systemd Service

```ini
[Unit]
Description=Saima AI Agent (Urdu ext 8000)
After=network.target asterisk.service

[Service]
Type=simple
User=asterisk
WorkingDirectory=/opt/aiagent
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/aiagent/venv/bin/python3 /opt/aiagent/saima.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 13. Critical Rules — DO NOT CHANGE

| Rule | Reason |
|---|---|
| `sleep(0.018)` NOT `0.020` | Prevents audio buffer drain → lag jitter |
| `_FILLERS_UR = []` | Silence sounds more human than filler audio |
| Barge-in threshold = 3 words | 2 is too sensitive, 4 is too slow |
| `utterance_end_ms=1000` | Shorter = cuts caller off, longer = awkward pause |
| Max 2-3 sentences per turn | Longer = TTS drones → caller hangs up |
| No markdown in prompt | TTS reads `**` and `|` aloud |
| Feminine verbs only (بتا رہی ہوں) | Mixed gender verbs sound disjointed |
| Saima returns after barge-in | Unlike English agent — partial transcripts cause Urdu parsing failures |
| `normalize_tts_text()` always called | Numbers and emails garble without it |
| `normalize_urdu_stt()` always called | LLM doesn't understand Urdu-script loanwords |

---

## 14. Testing Checklist

- [ ] Call agent — does greeting play naturally?
- [ ] Ask domain question — does response stay on-topic?
- [ ] Ask off-topic question — does domain guard fire correctly?
- [ ] Ask the same question 3 times — does repeat prevention trigger?
- [ ] Say goodbye — does agent exit gracefully without extra question?
- [ ] Interrupt during speaking — does barge-in stop playback within 20ms?
- [ ] Long silence (>3s) — does agent wait without saying "hello?"
- [ ] Check logs for USER:/SAIMA: turns — are responses 2-3 sentences?
- [ ] Check `journalctl -u saima -o cat --no-pager` — any errors?
- [ ] Call again — does returning caller context work?
- [ ] Ask for supervisor — does transfer tag fire?
