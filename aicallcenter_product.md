# AI Call Center — Product Spec

**Stack:** Asterisk/FreePBX (telephony) · Custom Python agents via AudioSocket · Deepgram Nova-3 (STT) · OpenAI GPT-4o-mini (conversation LLM) · Groq Llama-4 (extraction) · Deepgram Aura / Uplift / ElevenLabs (TTS) · Odoo 19 CE (CRM/Helpdesk) · Chatwoot (omnichannel inbox) · n8n (workflows) · Cal.com (scheduling) · Metabase + Grafana (analytics)

**Deployment model:** Single-tenant. Each client gets a Hostinger KVM 4GB ($5.19/mo) instance — cloned repo per client.

**Current client:** Punjab Sahulat Bazaar Authority (PSBA) — serves as the reference implementation.

---

## 1. Automated In-Call Actions

### 1.1 In-Call New Appointment Booking
- Live Cal.com availability query + book during conversation via agent tool.
- **Build:** Deploy Cal.com Docker → `search_slots()` + `book_appointment()` Python functions in agent toolchain → LLM calls them mid-call → confirmation read back via TTS.

### 1.2 Reschedule / Cancel Appointment via Voice
- Search booking by phone/name → confirm verbally → modify or delete via Cal.com API.
- **Build:** `search_booking()` → `modify_booking(booking_id, action)` → verbal confirmation step before executing DELETE.

### 1.3 In-Call Lead Qualification
- Extract intent/budget/urgency mid-conversation (currently done post-call via Groq).
- **Build:** Move `extract_lead_data()` call from `post_call_actions()` into `on_after_llm()` hook — fires after every N turns, updates live context.

### 1.4 In-Call CRM Mutation
- Write to Odoo mid-call (currently batched at call end via `asyncio.gather`).
- **Build:** Fire Odoo `write()` calls from `on_after_llm()` as facts are confirmed — fields update live on the contact record.

---

## 2. Unified Workspace & Voice CRM

### 2.1 Unified Omnichannel Workspace
- Chatwoot consolidating WhatsApp, Email, and Voice logs into one human-facing inbox.
- **Status:** Chatwoot already deployed. Needs WhatsApp + Email channel configs enabled.

### 2.2 Chronological Contact History
- Full multi-channel thread on Odoo `res.partner` — call logs, chat transcripts, ticket history.
- **Status:** ✅ Done. Post-call `asyncio.gather()` writes to Chatwoot + Odoo. Pre-call Chatwoot lookup injects history into LLM context.

### 2.3 Persistent Conversation Memory Across Sessions
- Pre-load caller's previous conversation history into LLM system prompt before voice streaming starts.
- **Status:** ✅ Done. `_on_call_setup()` queries Chatwoot for caller history by phone number → injects `## RETURNING CLIENT` context block into `caller_context`.

### 2.4 First-Touch AI Automation
- AI answers first on ALL channels — no waiting period, no human-first default.
- **Status:** ✅ Done on voice (agents answer first). Chatwoot bot config needed for text channels.

---

## 3. Workflows & Automation

### 3.1 Visual Workflow Builder
- Self-hosted n8n — 400+ integrations, full local control.
- **Build:** Deploy Docker container on EC2. Connect to Odoo, Chatwoot, ntfy webhooks.

### 3.2 AI-Powered Automation Scaffolding
- LLM extraction → structured fields → route downstream. Already works via Groq `extract_lead_data()`.
- **Status:** ✅ Done.

### 3.3 Call-Triggered Follow-up Chains
- Post-call disposition (Hot/Warm/Cold) → n8n triggers multi-channel cascade (WA/SMS/email).
- **Build:** n8n webhook from post-call pipeline → branches by `lead_temperature` → scheduled WA/SMS sequence.

### 3.4 Automated Appointment Reminders
- Cal.com → n8n → scheduled WhatsApp reminders X hours before appointment.
- **Build:** n8n polls Cal.com upcoming bookings → fires WA template messages via Chatwoot or direct API.

### 3.5 Missed-Call-Text-Back
- Asterisk dialplan captures `NOANSWER` → 60s delay in n8n → auto sends localized WhatsApp/SMS.
- **Build:** FreePBX `extensions_custom.conf` flag on `NOANSWER` → webhook to n8n → 60s Wait → send message.

---

## 4. Reporting & Dashboards

### 4.1 Voice AI Performance Dashboard
- Metabase reading Asterisk CDR + Odoo tables.
- **Build:** Deploy Docker → connect data sources → 5-metric dashboard: Total AI Calls, Transfer Rate %, Booking Success %, Avg Duration, Sentiment.

### 4.2 Operational Call Analytics
- Real-time call volumes, durations, trunk utilization — Grafana + Prometheus.
- **Build:** Deploy Grafana Docker + Prometheus exporter for Asterisk metrics.

### 4.3 Agent Performance Reports
- Human agent productivity, resolution rates — natively from Odoo Helpdesk reporting.
- **Status:** ✅ Done. Configure native Odoo dashboards.

---

## Build Priority

| Order | Feature | Est. Time | Depends On |
|---|---|---|---|
| 1 | Deploy n8n Docker | 30 min | — |
| 2 | Deploy Cal.com Docker + API config | 1 hr | — |
| 3 | Deploy Metabase Docker | 30 min | — |
| 4 | 1.1 In-Call Booking | 2 hr | Cal.com deployed |
| 5 | 1.2 Reschedule/Cancel | 2 hr | Cal.com deployed |
| 6 | 1.3 Mid-call lead qualification | 1 hr | — |
| 7 | 1.4 Mid-call CRM writes | 1 hr | — |
| 8 | 3.5 Missed-Call-Text-Back | 2 hr | n8n deployed |
| 9 | 3.4 Appointment Reminders | 1 hr | Cal.com + n8n |
| 10 | 2.1 Chatwoot WA/Email channels | 1 hr | — |
| 11 | 4.2 Grafana + Prometheus | 1 hr | — |

**Total new build: ~9-10 hours.**

---

## Infrastructure Decisions (Finalized)

| Decision | Choice | Rationale |
|---|---|---|
| Deployment | Single-tenant Hostinger KVM 4GB ($5.19/mo) | 15-20x cheaper than GHL. One VM per client. |
| Voice agents | Custom Python (AgentEngine) | Already built and running. No benefit migrating to Dograh/Pipecat. |
| STT | Deepgram Nova-3 | $0.0043/min. Existing integration works. faster-whisper deferred to v2. |
| Conversation LLM | OpenAI GPT-4o-mini ($0.40/mo) | Fast, cheap, good Urdu. |
| Extraction LLM | Groq Llama-4 (free tier) | 500K tokens/day free. Saves OpenAI credits. |
| TTS - EN | Deepgram Aura asteria-en | Included in Deepgram credits. |
| TTS - UR (Saima) | Uplift helpdesk-agent | Better natural Urdu than ElevenLabs for this use case. Pricing TBD. |
| TTS - UR (Zara) | ElevenLabs Sana ($5/mo shared) | Shared across clients. Better for receptionist tone. |
| Scheduling | Cal.com | Self-hosted. Full API control. |
| CRM | Odoo 19 CE | Self-hosted. XMLRPC integration working. |
| Omnichannel | Chatwoot | Self-hosted. Voice + text unified inbox. |
| Workflows | n8n | 400+ integrations. Self-hosted. |
| Dashboards | Metabase (business) + Grafana (ops) | Both self-hosted. |
| SIP trunk | Client pays directly | Not our cost. |
| GPU tools | v2 (faster-whisper, Qalb LLM, Qdrant RAG) | Not needed for MVP. Current tools suffice. |

## Cost Per Client

| Item | Cost/mo |
|---|---|
| Hostinger KVM 4GB | $5.19 |
| Deepgram STT (1,000 min) | $4.30 |
| OpenAI GPT-4o-mini | ~$0.40 |
| ElevenLabs (shared) | $0.50 (at 10 clients) |
| Domain + SSL | ~$0.83 |
| **Total** | **~$11.22** |

vs GHL Voice: $160-227/mo. **15-20x cheaper.**
