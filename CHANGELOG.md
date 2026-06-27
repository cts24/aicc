# Changelog

## v1.0.0 — 2026-06-27

### Product Launch — AI Voice Call Center

**Architecture:**
- 4-layer prompt system (core + expertise + context + KB)
- 6 specialized core persona files (receptionist, support EN/UR, sales EN/UR)
- 9 industry expertise modules (retail_marketplace, healthcare, education, real_estate, automotive, travel, banking, ecommerce, general)
- Runtime prompt assembly via `agent_lib/prompt_builder.py`

**5 AI Agents:**
| Agent | Ext | Role | Core Persona |
|---|---|---|---|
| Zara | 5000 | Bilingual Receptionist | `receptionist` |
| Saima | 8000 | Urdu Support/Complaints | `urdu_support` |
| Sana | 8500 | Urdu Sales Specialist | `urdu_sales` |
| Sara | 9000 | English Support/Complaints | `english_support` |
| Zoya | 9500 | English Sales Specialist | `english_sales` |

**Integrations:**
- Deepgram Nova-2/3 (STT), OpenAI GPT-4o-mini (LLM), Deepgram Aura / Uplift / ElevenLabs (TTS)
- Odoo 19 CRM (leads, helpdesk tickets, partner lookup)
- Chatwoot omnichannel (contact history, lead creation)
- ntfy push notifications, Gmail email reports
- Asterisk PJSIP + AMI (call control, transfers)
- AudioSocket 16kHz SLIN16 pipeline

**Deployment:**
- Single-tenant, git-based: `git clone` + 4 config files = fully operational
- systemd services for all 5 agents
- PSBA (Punjab Sahulat Bazaar Authority) as reference implementation

**Client Onboarding:**
- 8 data points collected from client → mapped to 4 files (client_config.env, client_context.txt, knowledge_base.txt, .env)
- Templates in `agent/templates/` for deploy team
- Industry selected via `INDUSTRY=` — no code changes

### Key Decisions
- Role-specialized personas instead of hybrid generalists
- Government/private sector handled in client_context.txt, not industry modules
- Industry = domain knowledge only (retail, healthcare, real estate, etc.)
- Full KB in prompt (no RAG) — fits in 4% of 128K context window. **RAG deferred to v1.1** — needed when KB exceeds 200KB+.
- Separate client_config.env from secrets (.env)

## v1.1 — Post-Launch (Priority Order)

### Agent Super-Tuning (99.9% accuracy target)
One agent at a time, starting with Saima:
1. Record real calls → identify every error per agent
2. Fix source of truth by error type:
   - Wrong facts (location, pricing, policy) → `knowledge_base.txt`
   - Robotic tone, unnatural language → `core/{type}_persona.txt` (voice rules)
   - Bad objection handling, transfer decisions → `core/{type}_persona.txt` (methodology)
   - Weak closing → `core/{type}_sales_persona.txt` (closing techniques)
   - Wrong routing → `client_context.txt` + receptionist persona
3. Role-play each fix → verify → repeat until 99.9%

### Feature Builds
- **RAG pipeline** — Qdrant + embeddings for clients with 200KB+ KB
- **In-call appointment booking** — Cal.com integration
- **Mid-call lead qualification** — extraction from post-call to live
- **Missed-call-text-back** — n8n workflow
- **Grafana + Prometheus** — live ops monitoring
