# Prompt Changelog — Saima (ext 8000)

## How to use this file
- Each entry documents one prompt iteration loop
- Format: change → deploy → test → result → lesson
- Rollbacks noted with reason

---

## v4 — 2026-06-23: TTS-friendly number/email/address formatting

### Changes
- Added "Numbers and addresses — TTS-friendly format" section with 9-row table
  showing wrong vs right format for phone, email, website, percentage, price, numbers
- Added `normalize_tts_text()` pre-processor in `agent_lib/speech.py`:
  - Emails: @ → ایٹ, dot → ڈاٹ
  - Phone numbers with hyphens → Urdu digit names with pauses
  - 10+ digit sequences → digit-by-digit spacing
- Extended Uplift phrase replacement config from 10→28 domain terms

### Result
- Numbers and addresses more clearly pronounced
- Some edge cases still need work (user says "need further improvements")

### Lesson
- Three-layer approach works: prompt + pre-processor + phrase config
- The pre-processor catches LLM mistakes; the prompt reduces mistakes

---

## v3 — 2026-06-23: Caller gender detection + Urdu verb conjugation

### Changes
- Added `detect_caller_gender()` in `agent_lib/speech.py`:
  - Regex-based detection from self-referential verbs (گیا/گئی, چاہتا/چاہتی)
  - Common Pakistani name markers (احمد/علی/فاطمہ/عائشہ)
- Added "Caller gender — verb forms match karein" section with table
- Added `caller_gender` field + `_detect_gender()` method in `CallHandler`
- Gender info injected into `caller_context` dynamically once detected

### Result
- Saima now addresses male callers with رہے ہیں/چاہتے ہیں and female with رہی ہیں/چاہتی ہیں
- Detection is automatic — caller doesn't need to state gender

### Lesson
- Regex-based detection is instant (no LLM call needed)
- First 1-2 turns might use default masculine before gender is detected
- Could be more sensitive — some callers don't use self-referential verbs early

---

## v2 — 2026-06-23: Fixed repetitive turn-yield + premature Allah Hafiz

### Changes
- Removed "End always with turn-yield" → "Kabhi kabhi turn-yield do, har baar nahi"
- Changed turn-yielding section from "caller ko batayein" → "kabhi kabhi, har baar nahi"
- Removed hardcoded "بہت شکریہ، اللہ حافظ!" from call flow step 6
- Added "ONLY when caller explicitly says goodbye / Allah Hafiz first"
- Fixed backchannel instruction from "har baar" → "kabhi kabhi"

### Result
- No more "ٹھیک ہے؟" after every sentence
- No more premature Allah Hafiz mid-conversation
- User: "much better"

### Lesson
- "Always" in prompts is dangerous — LLM takes it literally
- Explicit farewell wording in call flow teaches LLM to say it as routine closing
- `is_farewell_response` on Saima's own reply creates self-reinforcing loop

---

## v1 — 2026-06-23: 8 human-likeness voice patterns (initial rewrite)

### Changes
- Full system prompt rewrite with 8 research-backed patterns:
  1. Backchannels (جی, اچھا, ہوں)
  2. Discourse markers (تو, پھر, اب, یعنی)
  3. Calibrated disfluency (اہ, امم — 1-2 per call)
  4. Turn-yielding (ٹھیک ہے؟, سمجھ آ گیا نا؟)
  5. Prosody awareness (sentence rhythm variation)
  6. Emotion mirroring (match caller's tone)
  7. Verbal covers for dead air (ذرا رکیں، check کر لیتی ہوں)
  8. Floor-holding (partial repetition)
- Simplified from academic Urdu to Roman Urdu + English mixed instructions
- Consolidated Pakistani vs Indian Urdu patterns

### Issues found
- **Turn-yield overuse**: "End always with turn-yield" → "ٹھیک ہے؟" after every sentence
- **Premature Allah Hafiz**: Call flow taught LLM to say it as routine closing →
  triggers `is_farewell_response` on Saima's own output → farewell loop mid-call

### Lesson
- 8 patterns was too much at once — introduced multiple behavioral issues
- Roman Urdu instructions are effective but need careful wording
- Teaching farewell wording in prompt creates self-referential detection bug

---

## v5 — 2026-06-24: Markdown lockdown + domain guard + repeat prevention

### Changes
- **Prompt rewrite** — stripped bloat (8 patterns → concise sections), focused on core problems
- **NO MARKDOWN rule** at very top — bold, italic, lists, tables, headings banned
- **Domain guard** — explicit non-PSBA redirect: "معذرت، یہ PSBA helpline ہے"
- **Location answers** — never list, always ask area first, conversationally name 2-3
- **Repeat prevention** — 2nd repeat: "میں نے ابھی بتایا", 3rd repeat: helpline → end call
- **Response length** — max 2-3 sentences absolute
- **`normalize_tts_text()`** — added markdown stripping before number formatting
- **Graceful exit** — caller clearly done → no extra question, natural farewell
- Removed Pakistani/Indian comparison table to avoid table pattern leaking
- Removed number formatting tables (prompt was learning to output tables)
- Simplified: 255 lines of prompt → 78 lines (focused, enforced)

### Result
- LLM no longer outputs `**bold**` or `1. numbered lists` in speech
- Off-topic questions redirected to PSBA domain
- Location questions answered conversationally without list dumps
- Same info not repeated when caller asks again
- Call naturally ends after 3rd repeat of same question

### Lesson
- Tables and formatting examples in the prompt teach the LLM to output the same format
- Fewer patterns with strict enforcement beats many patterns with loose guidance
- Domain guard needs to be explicit with exact wording, not just a "no" rule

---

## v0 — Original (before 2026-06-23)

### State
- Formal/translated Urdu style
- No turn-yielding awareness
- No backchannels
- No gender awareness
- No TTS-friendly formatting
- Raw numbers and emails passed through

### Issues
- Sounded robotic and translated
- No natural conversation patterns
- Numbers and emails mispronounced by Uplift TTS
- No gender-aware verb conjugation
- Voice-drop + 1-4s lag (root cause: sleep timing + G.722 change)
