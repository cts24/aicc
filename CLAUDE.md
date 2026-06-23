# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project: PSBA AI Voice Call Center

**Client:** Punjab Sahulat Bazaar Authority (PSBA), Government of Punjab
Three AI voice agents on Asterisk. Caller dials → Asterisk AudioSocket bridges raw PCM → STT → LLM → TTS → audio back to caller.

---

## Infrastructure

| Field | Value |
|---|---|
| EC2 IP | `44.194.44.98` |
| EC2 OS | Amazon Linux 2023 |
| SSH User | `ec2-user` |
| SSH Key | `D:\Cloudops24\AICC\AICCkey.pem` |
| Asterisk | v20.18.2 compiled from source, PJSIP |
| Agent path | `/opt/aiagent/` |
| Python venv | `/opt/aiagent/venv/bin/python3` |
| Service user | `asterisk` |
| ffmpeg | `/usr/local/bin/ffmpeg` (static binary) |
| .env | `/opt/aiagent/.env` — `chmod 600`, owned by `asterisk` |
| nginx | Host-installed (not Docker), reverse proxy for HTTPS |
| SSL cert | **EXPIRED** 2026-06-20 — needs `sudo certbot renew` |
| Dashboard | `dashboard.py` on port 8080 (asterisk user) |

---

## Agents

| Ext | Agent | File | Port | UUID |
|---|---|---|---|---|
| 5000 | Zara — Bilingual Receptionist | `zara.py` (876 lines) | 9096 | `...000003` |
| 9000 | Sara — English Customer Service | `voice_agent.py` (634 lines) | 9092 | `...000001` |
| 8000 | Saima — Urdu Customer Service | `saima.py` (603 lines) | 9094 | `...000002` |
| 1000 | Owner (Zoiper/MicroSIP) | — | — | — |
| 1010-1003 | Additional SIP users | PJSIP | — | — |
| 2000 | Accounts dept | PJSIP | — | — |
| 3000 | Supervisor | PJSIP | — | — |
| 4000 | Support dept | PJSIP | — | — |

| Agent | STT | TTS | LLM (conversation) | LLM (extraction) |
|---|---|---|---|---|
| Zara | Deepgram Nova-3 `multi` | Deepgram Aura (EN) + ElevenLabs Sana (UR) | OpenAI `gpt-4o-mini` | OpenAI `gpt-4o-mini` |
| Sara | Deepgram Nova-2 `en-US` | Deepgram Aura `aura-asteria-en` | OpenAI `gpt-4o-mini` | **Groq `llama-4-scout-17b`** (free) |
| Saima | Deepgram Nova-3 `ur` | **Uplift TTS helpdesk-agent** + ffmpeg soxr 16kHz | OpenAI `gpt-4o-mini` | **Groq `llama-4-scout-17b`** (free) |

**Config is NOT hardcoded anymore** — all agents use `agent_lib/config.py` which loads from `.env`.
See `.env.example` for all available variables.

---

## agent_lib — Shared Library (13 modules)

| Module | Lines | Purpose |
|---|---|---|
| `audiosocket.py` | 28 | Protocol constants + `pack_frame`/`read_frame`/`downsample_16k_to_8k` |
| `phone.py` | 8 | `normalize_phone()` — E.164 conversion |
| `speech.py` | 100 | `is_farewell_response()`, `strip_gap_words()`, `urdu_phonetic()` |
| `config.py` | 191 | `AgentConfig` dataclass + `load_env()` + per-agent config builders |
| `log.py` | 12 | `setup_log()` — JSON-structured logging |
| `ami.py` | 262 | `AMIClient` — connect, Status, GetVar, Redirect, Originate |
| `chatwoot.py` | 114 | `chatwoot_lookup()`, `create_chatwoot_lead()` |
| `ntfy.py` | 34 | `send_ntfy_notification()` |
| `gmail.py` | 42 | `send_gmail_notification()` |
| `calendar.py` | 27 | `book_sales_appointment()` — wired, needs JSON key |
| `odoo.py` | 132 | `OdooClient` — async XMLRPC wrapper, `search_partner()`, `create_lead()` |
| `llm.py` | 127 | `llm_respond()`, `extract_name_phone()`, `extract_lead_data()`, `parse_transfer_tag()` |
| `__init__.py` | 14 | Re-exports all public API |

---

## Audio Pipeline — 16kHz SLIN16

```
Caller dials ext
      │  Answer() → Wait(1) → Set(GLOBAL(CALLERID)) → AudioSocket(UUID, 127.0.0.1:PORT)
      │
 asterisk_reader()   ← reads AudioSocket frames (HANGUP/UUID/AUDIO SLIN16)
      │                 drops audio when thinking=True OR transfer_in_progress=True
      │
 audio_queue         ← asyncio.Queue
      │
 dg_sender()         ← downsamples 16kHz→8kHz via decimation → Deepgram WebSocket
      │                 sends KeepAlive every 0.5s when idle
      │
 dg_receiver()       ← Results (is_final) → transcript_parts
      │                 UtteranceEnd → fires handle_transcript()
      │                 barge-in: text while speaking → barge_in.set()
      │
 handle_transcript() ← thinking=True → silence filler → llm_respond() → parse_transfer_tag()
      │                 if [TRANSFER:SUPERVISOR] → speak() → do_blind_transfer()
      │                 else → speak() → farewell check → offered_goodbye pattern
      │                 _capture_name_phone() runs as background task each turn
      │
  speak()             ← TTS while sending silence frames (640 bytes @ sleep 0.018)
      │                 Saima: Uplift TTS (WAV_22050_16) → ffmpeg soxr → PCM 16kHz
      │                 Sara:  Deepgram Aura (MP3) → ffmpeg soxr → PCM 16kHz
      │                 Zara:  Dg Aura (EN) or ElevenLabs MP3 (UR) → ffmpeg → PCM 16kHz
      │
 play_audio()        ← 640-byte chunks, sleep(0.018), AS_AUDIO_SLIN16 (0x12)
      │
 run() finally       ← fires post_call_actions() on ALL exits
```

**AudioSocket frames:** `[type:1B][length:2B BE][data]`
- `0x00` = HANGUP
- `0x01` = UUID  
- `0x10` = AUDIO (SLIN 8kHz) — not used
- `0x12` = AUDIO_SLIN16 — 16kHz PCM, standard
- `0xff` = ERROR

**Key timings:**
- 640 bytes per chunk = 320 samples = 20ms audio at 16kHz
- `asyncio.sleep(0.018)` = 18ms sleep for 20ms audio = 2ms buffer per chunk
- This builds ~400ms buffer over a 10s response, absorbing event loop jitter
- Original bug: sleep(0.020) + write overhead ≈ 21ms cycle for 20ms audio → buffer drains → "lag jurk"

---

## Zara — Bilingual Receptionist (ext 5000)

Entry point for all callers. Detects language, understands reason, routes accordingly.

**Language detection (`detect_language()`):**
- Arabic Unicode `\u0600–\u06ff` → Urdu
- Devanagari `\u0900–\u097f` → Urdu (Deepgram `multi` transcribes Urdu speech as Hindi script)
- ASCII keyword `"urdu"` / `"urdoo"` → Urdu
- On language switch: conversation history cleared + greeting spoken directly (LLM NOT called for triggering utterance)

**Routing:**
- Travel / Hajj / Umrah / booking / holiday / visa / tour → English → Sara (9000), Urdu → Saima (8000)
- Accounts / billing / payment → ext 2000
- Complaint / support → ext 4000
- Speak to supervisor → attended transfer to ext 3000 via AMI
- **Does NOT transfer to sales unless caller explicitly mentions travel**

**Attended transfer to supervisor (ext 3000):**
1. Speaks hold message → sets `thinking=True` + `transfer_in_progress=True`
2. AMI Originate → calls supervisor ext 3000
3. Tracks `OriginateResponse` event — exits race immediately on reject/no-answer
4. Supervisor answers → `Bridge()` fires → AudioSocket HANGUP → call ends cleanly
5. If no answer → 2s pause → second attempt
6. **Both attempts fail:** sets `supervisor_attempted=True` → notify via ntfy → ask caller name+number for callback
7. Callback window based on PKT time (UTC+5): 9am–1pm "within 30 min", 1pm–6pm "before close of business", outside hours "first thing tomorrow 9 AM"

**Zara phonetic normaliser (`urdu_phonetic()` from `agent_lib/speech.py`):**
- Runs on every Urdu TTS string before ElevenLabs
- `PSBA` → `پی ایس بی اے`, `Sahulat Bazaar` → `سہولت بازار`, etc.
- Also handles `@` → ` ایٹ `, `.pk` → ` ڈاٹ پی کے`, digit splitting

**Zara does NOT:** create Chatwoot leads, book calendar appointments, send leads ntfy, do CRM lookup

**AMI user:** `zara` / `ZaraAMI2025` (has `originate` write permission for supervisor transfer)

---

## Sara — English Customer Service (ext 9000)

**System prompt:** PSBA English customer service — warm, professional, concise
- Max 2 sentences for simple replies, more for informational
- Does NOT announce call recording (IVR plays this automatically before connecting)
- Supervisor transfer via `[TRANSFER:SUPERVISOR]` tag — same mechanism as Saima
- Lead extraction uses Groq (not OpenAI) to save credit

**TTS:** Deepgram Aura `aura-asteria-en` — MP3 → ffmpeg soxr → PCM 16kHz

---

## Saima — Urdu Customer Service (ext 8000)

**System prompt:** Natural conversational Urdu — rewritten to match Uplift helpdesk-agent style. Uses English loanwords naturally (call, team, delivery, product, app, discount, helpline). Sample dialogue embedded in prompt.

**TTS:** Uplift TTS `helpdesk-agent` (WAV_22050_16) → ffmpeg `aresample=16000:resampler=soxr:precision=28` → PCM 16kHz SLIN16

**`strip_gap_words()` and `urdu_phonetic()`:** These functions still exist in `agent_lib/speech.py` but are **NOT called** from Saima's `speak()` anymore. The LLM output is sent directly to TTS. They're retained for Zara's Urdu TTS pipeline and for backward compatibility.

**No filler audio during LLM thinking:** `_FILLERS_UR = []` — empty list. Uplift TTS voice handles natural prosody at the voice level.

**Hold music:** `/opt/aiagent/sounds/hold_music.raw` — 256000 bytes (8s PCM 16kHz, soxr upsampled from original 8kHz)

**Supervisor announcement:** `/opt/aiagent/sounds/zara-supervisor-announce.wav` — 418KB pre-generated WAV

**Barge-in:** 3+ Urdu words during speaking → triggers barge-in

---

## Post-Call Pipeline (Sara + Saima)

1. If < 2 turns AND `caller_phone` known → Chatwoot contact with `outcome=incomplete_call`
2. If ≥ 2 turns → `extract_lead_data()` via **Groq** (free tier — `llama-4-scout-17b`)
3. Phone fallback: if Groq missed phone → AMI `caller_phone` injected
4. **ntfy** → `psba_leads` topic (self-hosted on port 8090)
5. **Chatwoot** → create/update contact + conversation + transcript (private) + lead note
6. **Odoo** → create `crm.lead` with transcript + metadata
7. **Google Calendar** → callback booking *(pending Google service account JSON)*
8. **Gmail** → HTML email *(optional, blank = skipped)*

**Known issue — Odoo XMLRPC:** Transcript can contain characters invalid for XML, causing `ExpatError: not well-formed (invalid token)` in `create_lead()`. Need to sanitize transcript with XML character filtering before passing to XMLRPC.

---

## Supervisor Transfer ([TRANSFER:SUPERVISOR] tag)

**Used by:** Sara + Saima  
**Mechanism:** LLM embeds `[TRANSFER:SUPERVISOR]` in response text → `parse_transfer_tag()` strips tag → speaks response → `do_blind_transfer()` → plays hold music (8s) → AMI `Redirect` to ext 3000

**Transfer triggers (strict — only explicit conditions):**
1. Caller explicitly asks for supervisor / manager / human
2. Fraud — money already sent (caller confirms)
3. Complaint with health injury or major financial loss
4. Caller mentions FIA / court / legal action
5. Caller is highly distressed and can't calm them

**On failure:** Speaks graceful "lines busy" → takes name + number for callback. Call NEVER drops.

---

## Knowledge Base

`/opt/aiagent/knowledge_base.txt` — 8,456 chars, loaded into Sara + Saima system prompts.
Local: `agent/knowledge_base.txt`

**Contents:** PSBA overview, contact info (042-99001000, 03070002345, etc.), 12 Lahore bazaar locations, all 36 Punjab districts, product info (35% below market, prices change daily), app info, stall info, complaint channels.

---

## Docker Services (all self-hosted on EC2)

### Chatwoot CRM
| Field | Value |
|---|---|
| Internal URL | `http://44.194.44.98:3000` |
| Public HTTPS | `https://44-194-44-98.sslip.io` |
| Login email | `nextvisionorganization@gmail.com` |
| Account ID | 1, Inbox ID: 1 |
| API Token | `ADvi1PAFuxSxZbzZmF1SaPPf` |
| Install path | `/opt/chatwoot/` |
| Running | rails + sidekiq + redis + postgres (all up 7+ days) |

**Chatwoot API response:**
- Contact creation: id at `response.payload.contact.id`
- Conversation creation: id at `response.id` (top level)
- Transcript: `POST /conversations/{id}/messages` — `message_type: "outgoing"`, `private: true`
- Notes: `POST /contacts/{id}/notes`

### ntfy Push
| Field | Value |
|---|---|
| Internal server | `http://44.194.44.98:8090` |
| Public HTTPS server | `https://ntfy.44-194-44-98.sslip.io` |
| Running | Up 7 days |

| Topic | Trigger | Sent by |
|---|---|---|
| `psba_leads` | Every complete call end | Sara, Saima |
| `psba_supervisor` | Supervisor unavailable after 2 attempts | Zara |

### Odoo 19 CE
| Field | Value |
|---|---|
| Internal URL | `http://localhost:8069` |
| Public HTTPS | `https://odoo.44-194-44-98.sslip.io` **— NOT IN NGINX YET** |
| Default login | `admin` / `admin` |
| DB name | `odoo`, DB password: `odoo2025` |
| Install path | `/opt/odoo/` |
| Running | Up 2 hours (restarted recently) |

**Odoo client (`agent_lib/odoo.py`):**
- `OdooClient(url, db, username, password)` — async wrapper over sync XMLRPC via `run_in_executor`
- `search_partner(phone)` → lookup `res.partner` by phone
- `create_lead(lead, conversation, call_id, agent_name)` → creates `crm.lead`
- Lead mapping: `name` → `[Call] {name}`, `contact_name` → caller name, `phone` → caller phone, `description` → summary + transcript (last 20 turns) + metadata

**Odoo Docker:**
```bash
cd /opt/odoo
docker-compose ps
docker-compose logs -f
docker-compose restart
```

---

## nginx — HTTPS Reverse Proxy

| Field | Value |
|---|---|
| Config | `/etc/nginx/conf.d/aicc.conf` |
| SSL cert | `/etc/letsencrypt/live/44-194-44-98.sslip.io/fullchain.pem` |
| Cert expiry | **2026-06-20 — EXPIRED (needs `sudo certbot renew`)** |
| Routes configured | Chatwoot (`44-194-44-98.sslip.io`) + ntfy (`ntfy.44-194-44-98.sslip.io`) |
| Routes MISSING | **Odoo** (`odoo.44-194-44-98.sslip.io` → localhost:8069) not added yet |

**Renew SSL:**
```bash
sudo certbot renew
sudo systemctl restart nginx
```

---

## Sleep Timing — Event Loop Jitter Compensation

All audio loops use `sleep(0.018)` for 640-byte chunks (20ms audio at 16kHz):

```python
# play_audio and speak silence loops in all 3 agents:
await asyncio.sleep(0.018)  # 18ms sleep for 20ms audio = 10% buffer
```

**Why 0.018 and not 0.020:**
- 640 bytes = 320 samples = 20ms of audio at 16kHz
- `asyncio.sleep(0.020)` has ~1-5ms jitter from event loop overhead
- With write+drain (~1ms), actual cycle = 21-26ms per 20ms chunk → buffer slowly drains
- `sleep(0.018)` gives 2ms slack per chunk → builds buffer → absorbs jitter
- This was the original approach before the 0.020 "fix" caused the lag jitter

---

## G.722 Wideband Audio

Enabled on all PJSIP endpoints (codec order: g722 first, then ulaw):

```ini
[1000]
disallow=all
allow=g722
allow=ulaw
```

Same pattern on all 8 endpoints (1000-4000, 1010, 1001-1003).

**Zoiper paid tier** required for G.722. MicroSIP (UDP 5060) ulaw only.

---

## Systemd Services

| Service | Controls | File |
|---|---|---|
| `aiagent` | Sara ext 9000 | `agent/aiagent.service` |
| `saima` | Saima ext 8000 | `agent/saima.service` |
| `zara` | Zara ext 5000 | `agent/zara.service` |
| `nginx` | HTTPS reverse proxy | host systemd |

All 3 agents running as `asterisk` user, `Restart=always`, `RestartSec=5`.

```bash
# Status
sudo systemctl status aiagent saima zara nginx

# Restart agents
sudo systemctl restart aiagent saima zara

# Live logs all agents
sudo journalctl -u aiagent -u saima -u zara -o cat -f

# Single agent recent logs
sudo journalctl -u aiagent -o cat --no-pager --since '10 minutes ago'
sudo journalctl -u saima  -o cat --no-pager --since '10 minutes ago'
sudo journalctl -u zara   -o cat --no-pager --since '10 minutes ago'
```

---

## SSH & Deploy

```powershell
# SSH
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98

# Deploy all agents
scp -i "D:\Cloudops24\AICC\AICCkey.pem" agent/voice_agent.py agent/saima.py agent/zara.py ec2-user@44.194.44.98:/tmp/
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98 "sudo cp /tmp/voice_agent.py /tmp/saima.py /tmp/zara.py /opt/aiagent/ && sudo systemctl restart aiagent saima zara"

# Deploy agent_lib
scp -i "D:\Cloudops24\AICC\AICCkey.pem" -r agent_lib/ ec2-user@44.194.44.98:/tmp/agent_lib/
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98 "sudo cp -r /tmp/agent_lib/* /opt/aiagent/agent_lib/ && sudo chown -R asterisk:asterisk /opt/aiagent/agent_lib/ && sudo systemctl restart aiagent saima zara"
```

---

## Asterisk Configuration

### `/etc/asterisk/extensions.conf`
```ini
[from-internal]
exten => 1000,1,Dial(PJSIP/1000,30)
exten => 1000,n,Hangup()
exten => 1010,1,Dial(PJSIP/1010,30)
exten => 1010,n,Hangup()
exten => 9000,1,Answer()
exten => 9000,n,Wait(1)
exten => 9000,n,Set(GLOBAL(SARA_CALLERID)=${CALLERID(num)})
exten => 9000,n,AudioSocket(00000000-0000-0000-0000-000000000001,127.0.0.1:9092)
exten => 9000,n,Hangup()
exten => 8000,1,Answer()
exten => 8000,n,Wait(1)
exten => 8000,n,Set(GLOBAL(SAIMA_CALLERID)=${CALLERID(num)})
exten => 8000,n,AudioSocket(00000000-0000-0000-0000-000000000002,127.0.0.1:9094)
exten => 8000,n,Hangup()
exten => 5000,1,Answer()
exten => 5000,n,Wait(1)
exten => 5000,n,Set(GLOBAL(ZARACHAN)=${CHANNEL})
exten => 5000,n,AudioSocket(00000000-0000-0000-0000-000000000003,127.0.0.1:9096)
exten => 5000,n,Hangup()
exten => 2000,1,Dial(PJSIP/2000,30)
exten => 2000,n,Playback(vm-nobodyavail)
exten => 2000,n,Hangup()
exten => 3000,1,Dial(PJSIP/3000,30)
exten => 3000,n,Hangup()
exten => 4000,1,Dial(PJSIP/4000,30)
exten => 4000,n,Playback(vm-nobodyavail)
exten => 4000,n,Hangup()

[zara-supervisor]
exten => check,1,Answer()
exten => check,n,Wait(1)
exten => check,n,Playback(/opt/aiagent/sounds/zara-supervisor-announce)
exten => check,n,WaitExten(20)
exten => check,n,Hangup()
exten => 1,1,Bridge(${BRIDGETO},p)
exten => 1,n,Hangup()
```

### `/etc/asterisk/manager.conf`
```
[zara] secret=ZaraAMI2025  — read: system,call,reporting,originate  — write: system,call,originate
[sara] secret=SaraAMI2025  — read: system,call,reporting           — write: system,call
[saima] secret=SaimaAMI2025 — read: system,call,reporting          — write: system,call
```

### PJSIP Endpoints
8 endpoints (1000–4000, 1010, 1001–1003). All have G.722 first. Passwords follow pattern `{Extension}2025!` (e.g. `Times2025!` for ext 1000).

---

## Key Design Decisions

- **All config in .env** via `agent_lib/config.py` — no more hardcoded keys in agent files
- **16kHz audio pipeline** with soxr resampler for highest quality
- **AudioSocket SLIN16 (0x12)** for 16kHz PCM (was 0x10 = 8kHz SLIN)
- **8kHz Deepgram STT** — incoming 16kHz audio downsampled to 8kHz via decimation before STT
- **sleep(0.018)** for event loop jitter compensation — builds 2ms buffer per chunk
- **OpenAI for conversation** (latency critical), **Groq for extraction** (free tier, not latency critical)
- **[TRANSFER:SUPERVISOR] tag** — LLM-triggered blind transfer via AMI Redirect
- **`strip_gap_words()` and `urdu_phonetic()`** functions exist but NOT called from Saima — removed because they mangled natural LLM output. Still used by Zara.
- **No filler audio** during LLM thinking — `_FILLERS_UR = []`

---

## API Keys & Services Status

| Service | Used By | Status |
|---|---|---|
| Deepgram STT (Nova-3) | All agents | Active — ~$197 prepaid |
| Deepgram TTS Aura | Zara EN, Sara | Active — included in prepaid |
| OpenAI GPT-4o-mini | All agents (conversation) | Active — ~$5 remaining |
| Groq Llama-4-Scout-17B | Sara, Saima (extraction) | Active — free (500K TPD) |
| ElevenLabs Sana Flash v2.5 | Zara UR only | Active — $5/mo, 15K chars left |
| Uplift TTS helpdesk-agent | Saima | Active — pricing TBD |
| ntfy (self-hosted) | All agents | Active — free |
| Chatwoot (self-hosted) | Sara, Saima | Active — free |
| Odoo 19 CE (self-hosted) | Sara, Saima | Active — XMLRPC error needs fix |
| Google Calendar | Sara, Saima | Wired, needs JSON key |
| WhatsApp Business API | All agents (planned) | Pending subscription |

---

## Known Issues

| Issue | Severity | Fix |
|---|---|---|
| **SSL cert expired** (2026-06-20) | HIGH — Chatwoot/ntfy HTTPS broken | `sudo certbot renew && sudo systemctl restart nginx` |
| **Odoo XMLRPC** — invalid XML chars in transcript → ExpatError | MEDIUM — Odoo leads not created | Sanitize transcript with `re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', transcript)` in `odoo.py:create_lead()` |
| **Odoo route missing from nginx** | MEDIUM — no HTTPS for Odoo | Add server block for `odoo.44-194-44-98.sslip.io` → localhost:8069 |
| **Lag jitter** from sleep(0.020) | FIXED — changed to 0.018 | Deployed |
| **Saima system prompt** outdated sections in CHECKPOINTS.md | Documentation | Updated this file |

---

## SIP Phone

```
Server: 44.194.44.98  |  Username: 1000  |  Password: Times2025!
Zoiper: TLS port 5061, SRTP on, verify cert OFF
MicroSIP: UDP port 5060
```

---

## AWS Security Group — Open Ports

| Port | Service | Access |
|---|---|---|
| 22 | SSH | Your IP |
| 80 | HTTP (nginx — Let's Encrypt validation) | 0.0.0.0/0 |
| 443 | HTTPS (nginx — Chatwoot + ntfy) | 0.0.0.0/0 |
| 5060 | SIP UDP (MicroSIP) | Your IP |
| 5061 | SIP TLS (Zoiper) | Your IP |
| 3000 | Chatwoot direct | Your IP |
| 8090 | ntfy direct | Your IP |
| 8080 | Dashboard | Your IP |
| 8069 | Odoo direct | Your IP |

---

## Pending Activations

| Item | What's needed | Status |
|---|---|---|
| Google Calendar | Upload `google_service_account.json` to `/opt/aiagent/` + pip install google libraries + set GOOGLE_CALENDAR_ID | Wired, needs JSON |
| WhatsApp Business API | Get API credentials → integrate post-call message | Planned |
| SSL renewal | `sudo certbot renew` (expired June 20) | Needs action |
| Odoo nginx route | Add server block for `odoo.44-194-44-98.sslip.io` | Needs action |
| Odoo XML sanitize | Fix `create_lead()` to strip invalid XML chars | Needs action |
