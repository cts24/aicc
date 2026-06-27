# CLAUDE.md

This file provides guidance when working with this repository.

---

## Project: AI Voice Call Center

**Stack:** Asterisk/FreePBX (telephony) · Custom Python agents via AudioSocket · Deepgram STT · OpenAI GPT-4o-mini (conversation LLM) · Groq Llama-4 (extraction) · Deepgram Aura / Uplift / ElevenLabs (TTS) · Odoo 19 CE (CRM/Helpdesk) · Chatwoot (omnichannel inbox) · n8n (workflows) · Cal.com (scheduling) · Metabase + Grafana (analytics)

**Deployment model:** Single-tenant. Each client gets their own instance.

**Current client:** Punjab Sahulat Bazaar Authority (PSBA), Government of Punjab — serves as the reference implementation.

---

## Infrastructure — Current (EC2)

| Field | Value |
|---|---|
| Server | m7i-flex.large (2 vCPU, 8GB RAM) |
| Public IP | `44.194.44.98` |
| OS | Amazon Linux 2023 |
| SSH User | `ec2-user` |
| SSH Key | `D:\Cloudops24\AICC\AICCkey.pem` |
| Asterisk | v20.18.2 compiled from source, PJSIP |
| Agent path | `/opt/aiagent/` |
| Python | `/opt/aiagent/venv/bin/python3` |
| Service user | `asterisk` |
| ffmpeg | `/usr/local/bin/ffmpeg` (static binary) |
| .env | `/opt/aiagent/.env` — `chmod 600`, owned by `asterisk` |
| nginx | Host-installed, reverse proxy for HTTPS |
| SSL | Valid until 2026-09-21 (86 days) |
| Dashboard | `dashboard.py` on port 8080 |

### Target Production — Hostinger KVM

| Field | Value |
|---|---|
| Plan | Hostinger KVM 4GB |
| Cost | **$5.19/mo** |
| Includes | 4GB RAM, 2 vCPU, storage, no EBS nonsense |

---

## Agents (PSBA Reference)

| Ext | Agent | File | Port | Language |
|---|---|---|---|---|
| 5000 | Zara — Bilingual Receptionist | `zara.py` | 9096 | EN + UR |
| 8000 | Saima — Urdu Support/Complaints | `saima.py` | 9094 | UR |
| 8500 | Sana — Urdu Sales Specialist | `sana.py` | 9099 | UR |
| 9000 | Sara — English Support/Complaints | `voice_agent.py` | 9092 | EN |
| 9500 | Zoya — English Sales Specialist | `zoya.py` | 9098 | EN |
| 1000 | Owner SIP | — | — | — |
| 2000 | Accounts dept | PJSIP | — | — |
| 3000 | Supervisor | PJSIP | — | — |
| 4000 | Support dept | PJSIP | — | — |

| Agent | Role | STT | TTS | Core Persona |
|---|---|---|---|---|
| Zara | Receptionist | Nova-3 `multi` | Deepgram Aura + ElevenLabs | `receptionist` / `receptionist_ur` |
| Saima | Urdu Support | Nova-3 `ur` | Uplift TTS | `urdu_support` |
| Sara | English Support | Nova-2 `en-US` | Deepgram Aura | `english_support` |
| Sana | Urdu Sales | Nova-3 `ur` | Uplift TTS | `urdu_sales` |
| Zoya | English Sales | Nova-2 `en-US` | Deepgram Aura | `english_sales` |

**Config** — all agents use `agent_lib/config.py` which loads from `.env`. See `.env.example`.

---

## agent_lib — Shared Library

| Module | Purpose |
|---|---|
| `engine.py` | `AgentEngine` base class — shared audio pipeline, Deepgram lifecycle, AMI, transfers, hooks |
| `config.py` | `AgentConfig` dataclass + `load_env()` + per-agent config builders |
| `audiosocket.py` | Protocol constants + `pack_frame`/`read_frame`/`downsample_16k_to_8k` |
| `ami.py` | `AMIClient` — connect, Status, GetVar, Redirect, Originate |
| `llm.py` | `llm_respond()`, `extract_name_phone()`, `extract_lead_data()`, `parse_transfer_tag()` |
| `odoo.py` | `OdooClient` — async XMLRPC wrapper, `create_lead()`, `create_helpdesk_ticket()`, `search_partner()`, `search_tickets_by_phone()` |
| `chatwoot.py` | `chatwoot_lookup()`, `create_chatwoot_lead()` |
| `ntfy.py` | `send_ntfy_notification()` |
| `gmail.py` | `send_gmail_notification()` |
| `calendar.py` | `book_sales_appointment()` — wired, needs Cal.com API |
| `speech.py` | `normalize_tts_text()`, `is_farewell_response()`, `urdu_phonetic()` |
| `phone.py` | `normalize_phone()` — E.164 conversion |
| `log.py` | `setup_log()` — JSON-structured logging |
| `__init__.py` | Re-exports all public API |

---

## Audio Pipeline

```
Caller dials ext → Answer() → AudioSocket(UUID, 127.0.0.1:PORT)

asterisk_reader()    → reads 16kHz SLIN16 frames → audio_queue
dg_sender()          → downsamples to 8kHz → Deepgram WebSocket
dg_receiver()        → Results (is_final) → transcript_parts
                      UtteranceEnd → handle_transcript()
handle_transcript()  → LLM → TTS → play_audio()
play_audio()         → 640B chunks, sleep(0.018), AS_AUDIO_SLIN16 (0x12)
```

**Frames:** `[type:1B][length:2B BE][data]` — `0x00`=HANGUP, `0x01`=UUID, `0x10`=8kHz AUDIO, `0x12`=16kHz SLIN16, `0xff`=ERROR

**Timing:** 640 bytes = 20ms audio. `sleep(0.018)` builds 2ms buffer per chunk to absorb event loop jitter.

---

## Docker Services — Running on EC2

| Service | Internal URL | Public HTTPS | Container |
|---|---|---|---|
| Chatwoot | `http://44.194.44.98:3000` | `https://44-194-44-98.sslip.io` | chatwoot/chatwoot:latest |
| ntfy | `http://44.194.44.98:8090` | `https://ntfy.44-194-44-98.sslip.io` | binwiederhier/ntfy |
| Odoo 19 | `http://localhost:8069` | `https://odoo.44-194-44-98.sslip.io` | odoo:19.0 |
| n8n | ❌ Not deployed | — | — |
| Cal.com | ❌ Not deployed | — | — |
| Metabase | ❌ Not deployed | — | — |
| Grafana | ❌ Not deployed | — | — |

### Chatwoot
- Login: `nextvisionorganization@gmail.com`
- API Token: `ADvi1PAFuxSxZbzZmF1SaPPf`
- Account ID: 1, Inbox ID: 1
- Contact creation: `response.payload.contact.id`
- Conversation creation: `response.id` (top level)
- Install: `/opt/chatwoot/`

### Odoo
- Login: `admin` / `admin`
- DB: `odoo` / `odoo2025`
- Install: `/opt/odoo/`
- Odoo Server 19.0-20260619
- `_xml_safe_str()` already implemented in `odoo.py` for XML char sanitization

### ntfy
- Topics: `psba_leads` (call leads) · `psba_supervisor` (supervisor unavailable)
- Server: `http://44.194.44.98:8090` / `https://ntfy.44-194-44-98.sslip.io`

---

## nginx HTTPS

| Domain | Proxies To | SSL Status |
|---|---|---|
| `44-194-44-98.sslip.io` | localhost:3000 (Chatwoot) | ✅ Valid to 2026-09-21 |
| `ntfy.44-194-44-98.sslip.io` | localhost:8090 | ✅ Valid to 2026-09-21 |
| `odoo.44-194-44-98.sslip.io` | localhost:8069 | ✅ Valid to 2026-09-21 |

Configs: `/etc/nginx/conf.d/aicc.conf` + `/etc/nginx/conf.d/odoo.conf`

---

## Systemd Services

| Service | Controls |
|---|---|
| `aiagent` | Sara ext 9000 |
| `saima` | Saima ext 8000 |
| `zara` | Zara ext 5000 |
| `nginx` | HTTPS reverse proxy |

All agents run as `asterisk` user, `Restart=always`.

### Commands
```bash
sudo systemctl status aiagent saima zara nginx
sudo systemctl restart aiagent saima zara
sudo journalctl -u aiagent -u saima -u zara -o cat -f
```

---

## Per-Client Cost Breakdown

| Item | Cost/mo | Who Pays | Notes |
|---|---|---|---|
| Hostinger KVM 4GB | **$5.19** | Us | 1 per client |
| Deepgram STT Nova-3 (1,000 min) | **$4.30** | Us | $0.0043/min — target: self-host faster-whisper |
| OpenAI GPT-4o-mini | **~$0.40** | Us | Negligible |
| ElevenLabs (Urdu TTS) | **$5 ÷ #clients** | Us | Shared — $0.50/client at 10 clients |
| SIP trunk | **$0 (client pays)** | Client | Their direct cost |
| Domain + SSL | **~$0.83** | Us | $10/yr |
| Uplift TTS | **TBD** | Us | Pricing not confirmed |
| Open source (everything else) | **$0** | — | |

**Total: ~$10.72/client/month** at 1,000 minutes.
**15-20x cheaper than GHL** ($160-227/mo).

---

## Product Features — MVP v1 (All 15)

### Section 1: Automated In-Call Actions

| # | Feature | Status | Build Effort |
|---|---|---|---|
| 1.1 | **In-Call Appointment Booking** — Cal.com availability query + book mid-call | ❌ New | Deploy Cal.com Docker + wire API into agent tools |
| 1.2 | **⭐ Reschedule/Cancel via Voice** — search booking → confirm → modify/delete | ❌ New | Cal.com API + verbal confirmation flow |
| 1.3 | **In-Call Lead Qualification** — intent/budget/urgency extraction mid-conversation | 🔄 Shift | Move `extract_lead_data()` from post-call to mid-call |
| 1.4 | **In-Call CRM Mutation** — Odoo `write()` during call | 🔄 Shift | Fire writes mid-call instead of batch at end |

### Section 2: Unified Workspace & Voice CRM

| # | Feature | Status | Build Effort |
|---|---|---|---|
| 2.1 | **Unified Omnichannel** — Chatwoot for WA/Email/FB/IG/Voice | 🔄 Minor | Enable WhatsApp + Email channels in Chatwoot |
| 2.2 | **Chronological Contact History** — all interactions on Odoo `res.partner` | ✅ Done | Post-call writes to Chatwoot + Odoo working |
| 2.3 | **⭐ Persistent Memory** — pre-load caller history into LLM context | ✅ Done | Chatwoot history injected via `_on_call_setup()` |
| 2.4 | **First-Touch AI** — AI answers first on all channels | ✅ Done | Agents answer first on voice. Chatwoot bot config for text |

### Section 3: Workflows & Automation

| # | Feature | Status | Build Effort |
|---|---|---|---|
| 3.1 | **Visual Workflow Builder** — self-hosted n8n | ❌ New | Deploy Docker |
| 3.2 | **AI-Powered Automation** — LLM extraction → structured fields | ✅ Done | Groq `extract_lead_data()` already does this |
| 3.3 | **Call-Triggered Follow-up Chains** — post-call → multi-channel cascade | 🔄 Partial | Current post-call gather() works. n8n for WA/SMS/email |
| 3.4 | **Automated Appointment Reminders** — Cal.com → n8n → scheduled WA | ❌ New | n8n + Cal.com webhook |
| 3.5 | **⭐ Missed-Call-Text-Back** — NOANSWER → 60s → WhatsApp/SMS | ❌ New | Dialplan + n8n |

### Section 4: Reporting & Dashboards

| # | Feature | Status | Build Effort |
|---|---|---|---|
| 4.1 | **⭐ Voice AI Performance Dashboard** — Metabase on Odoo + CDR | ❌ New | Deploy Docker + 5-metric query |
| 4.2 | **Operational Call Analytics** — Grafana live ops view | ❌ New | Deploy Docker + Prometheus exporter |
| 4.3 | **Agent Performance Reports** — Odoo Helpdesk reporting | ✅ Done | Configure native Odoo dashboards |

---

## Build Roadmap

| Order | Feature | Est. Time | Depends On |
|---|---|---|---|
| 1 | Deploy n8n Docker | 30 min | Nothing |
| 2 | Deploy Cal.com Docker + API config | 1 hr | Nothing |
| 3 | Deploy Metabase Docker + connect Odoo+CDR | 30 min | Nothing |
| 4 | 1.1 In-Call Booking — agent tool for Cal.com | 2 hr | Cal.com deployed |
| 5 | 1.2 Reschedule/Cancel — search+modify tools | 2 hr | Cal.com deployed |
| 6 | 1.3 Mid-call lead qualification | 1 hr | Nothing |
| 7 | 1.4 Mid-call CRM writes | 1 hr | Nothing |
| 8 | 3.5 Missed-Call-Text-Back — dialplan + n8n | 2 hr | n8n deployed |
| 9 | 3.4 Appointment Reminders — n8n workflow | 1 hr | Cal.com + n8n deployed |
| 10 | Verifying Chatwoot WA/Email channels config | 1 hr | Nothing |
| 11 | 4.2 Grafana + Prometheus | 1 hr | Nothing |

**Total new build: ~9-10 hours.** All 15 features present in v1.

---

## Deploy Commands

```powershell
# SSH
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98

# Deploy agents
scp -i "D:\Cloudops24\AICC\AICCkey.pem" agent/voice_agent.py agent/saima.py agent/zara.py ec2-user@44.194.44.98:/tmp/
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98 "sudo cp /tmp/voice_agent.py /tmp/saima.py /tmp/zara.py /opt/aiagent/ && sudo systemctl restart aiagent saima zara"

# Deploy agent_lib
scp -i "D:\Cloudops24\AICC\AICCkey.pem" -r agent_lib/ ec2-user@44.194.44.98:/tmp/agent_lib/
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98 "sudo cp -r /tmp/agent_lib/* /opt/aiagent/agent_lib/ && sudo chown -R asterisk:asterisk /opt/aiagent/agent_lib/ && sudo systemctl restart aiagent saima zara"

# Deploy engine.py only
scp -i "D:\Cloudops24\AICC\AICCkey.pem" agent_lib/engine.py ec2-user@44.194.44.98:/tmp/engine.py
ssh -i "D:\Cloudops24\AICC\AICCkey.pem" ec2-user@44.194.44.98 "sudo cp /tmp/engine.py /opt/aiagent/agent_lib/engine.py && sudo chown asterisk:asterisk /opt/aiagent/agent_lib/engine.py && sudo systemctl restart aiagent saima zara"
```

---

## Prompt Architecture — Layered System (v2)

Prompts are assembled at runtime from **4 layers** in `agent_lib/prompt_builder.py`:

```
Layer  —  PICCO  —  Source file          —  Who owns it
────────────────────────────────────────────────────────────
1        Persona    core/{type}_persona.txt     Our IP (never changes)
2        Context    expertise/{industry}.txt    Built once per industry  
3        Context    prompts/client_context.txt  Per-client (gitignored)
4        Context    prompts/knowledge_base.txt  Per-client (gitignored)
```

Placeholder substitutions at runtime:
```
{AGENT_NAME}     → cfg.agent_name (per-agent)
{COMPANY_NAME}   → cfg.company_name (.env)
{HELPLINE}       → cfg.helpline (.env)
{CLIENT_CONTEXT} → prompts/client_context.txt
{EXPERTISE}      → expertise/{industry}.txt
{KNOWLEDGE_BASE} → prompts/knowledge_base.txt
```

### Agent file → core persona mapping

| Agent | Type | Core file | Expertise | Industry config |
|---|---|---|---|---|
| Saima (ext 8000) | `urdu_agent` | `core/urdu_agent_persona.txt` | `expertise/{industry}.txt` | `INDUSTRY=public_sector` |
| Sara (ext 9000) | `english_agent` | `core/english_agent_persona.txt` | `expertise/{industry}.txt` | `INDUSTRY=public_sector` |
| Zara (ext 5000) | `receptionist` | `core/receptionist_persona.txt` | (none) | (none) |

### For a new client (deploy team workflow):

```
1. git clone <repo> /opt/aiagent/
2. Choose industry → set INDUSTRY=real_estate in .env
3. Fill prompts/client_context.txt (agent name, company, contact, routing)
4. Fill prompts/knowledge_base.txt (products, locations, prices)
5. Fill .env (API keys, integrations, SIP trunk)
6. systemctl restart aiagent saima zara
```

### To add a new industry module:
```
Create expertise/{industry_name}.txt following the PQP pattern:
  - ## Persona: domain-specific role
  - ## Key Knowledge: industry facts and terminology
  - ## Local Expertise: Pakistan-specific knowledge (if applicable)
  - ## Sales/Support Methodology: domain-specific techniques
```

### To pull framework updates:
```bash
git pull
sudo systemctl restart aiagent saima zara
```

---

## Git Workflow

**Branch strategy:** Simple GitHub Flow
- `main` = always production-ready, tested code
- `feature/<name>` = branch off main, work, PR merge back
- `fix/<name>` = hotfixes
- Tag releases: `v1.0.0`, `v1.1.0`

**What goes in git:**
| In git (framework) | NOT in git (per-client) |
|---|---|
| `agent_lib/` — shared engine | `.env` — secrets |
| `agent/*.py` — agent entry points | `*.pem`, `*.key` — certs |
| `agent/*.service` — systemd | `agent/prompts/` — client context + KB |
| `agent/core/` — persona + skills (our IP) | |
| `agent/expertise/` — industry modules | |
| `agent/templates/` — deploy team blueprints | |
| `deploy/` — Docker configs | |
| `lambda_code/` — Lambda scheduler | |
| `agent/dashboard.py` | |

### New client deployment:
```bash
# On client's Hostinger KVM:
git clone <repo> /opt/aiagent/
# Then replace: prompts/client_context.txt, prompts/knowledge_base.txt, .env
# Configure Asterisk, Docker services, SSL
# systemctl start aiagent saima zara
# Test call → live
```

### Framework updates to existing clients:
```bash
# On client's KVM:
cd /opt/aiagent && git pull   # only .py + agent_lib changed
sudo systemctl restart aiagent saima zara
```

---

## Post-Deploy Automation (for scaling)

```
Packer → build golden AMI with pre-installed stack
Terraform → deploy AMI + networking + RDS + Cloudflare DNS
Ansible → post-deploy config (prompts, KB import, SSL check)
Cloudflare Tunnel → secure by default, no public SSH
```

---

## Key Design Decisions

- **Shared AgentEngine base class** eliminates ~70% code duplication across agents
- **16kHz audio pipeline** with soxr resampler
- **AudioSocket SLIN16 (0x12)** for 16kHz PCM
- **8kHz Deepgram STT** — incoming 16kHz audio downsampled via decimation
- **sleep(0.018)** for event loop jitter compensation
- **GPT-4o-mini for conversation** (latency critical), **Groq for extraction** (free tier)
- **[TRANSFER:SUPERVISOR] tag** — LLM-triggered blind transfer via AMI Redirect
- **Open source first** — 90% of stack is free. Cost centers: compute ($5.19) + Deepgram ($4.30)
- **MVP v1 ships all 15 features** — no feature deferred. Latency/RAG/GPU tools in v2.
- **Single-tenant** — cloned instance per client, no multi-tenant complexity

---

## PSBA-Specific Reference

### SIP Phone
```
Server: 44.194.44.98  |  Username: 1000  |  Password: Times2025!
Zoiper: TLS port 5061, SRTP on, verify cert OFF
MicroSIP: UDP port 5060
```

### Security Group Ports
| Port | Service | Access |
|---|---|---|
| 22 | SSH | Your IP |
| 80/443 | HTTP/HTTPS | 0.0.0.0/0 |
| 5060/5061 | SIP | Your IP |
| 3000 | Chatwoot | Your IP |
| 8090 | ntfy | Your IP |
| 8080 | Dashboard | Your IP |
| 8069 | Odoo | Your IP |
