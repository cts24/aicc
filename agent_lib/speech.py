"""Speech detection utilities — farewell, gap words, Urdu phonetic normaliser."""
import re

_FAREWELL_RE = re.compile(
    r'(اللہ\s*حافظ|خدا\s*حافظ|allah\s*hafiz|khuda\s*hafiz|goodbye|good\s*bye)',
    re.IGNORECASE,
)

_EN_FAREWELL_RE = re.compile(
    r'\b(allah\s*hafiz|goodbye|good\s*bye|take\s*care|safe\s*travels?|'
    r'have\s*a\s*(great|good|safe|wonderful|lovely)\s*(day|trip|journey)?|'
    r'thank\s*you\s*for\s*calling|thanks\s*for\s*calling|speak\s*(to\s*you\s*)?soon|'
    r'all\s*the\s*best|khuda\s*hafiz)\b',
    re.IGNORECASE,
)


# ── Urdu gender detection ───────────────────────────────────────────────────
_MALE_VERBS = re.compile(
    r'\b(?:'
    r'میں\s+\S*\s*(?:گیا|چاہتا|کرتا\s*ہوں|رہا\s*ہوں|آیا|لیا|بتایا|'
    r'دیکھا|سنا|بولا|پوچھتا|جاتا|آتا|سکتا|ہوتا|لگتا|'
    r'لے\s*گیا|کہا|پڑھا|سمجھا|مانگا)'
    r'|چاہتا\s*ہوں|کرتا\s*ہوں|رہا\s*ہوں|سکتا\s*ہوں'
    r')\b',
    re.UNICODE,
)

_FEMALE_VERBS = re.compile(
    r'\b(?:'
    r'میں\s+\S*\s*(?:گئی|چاہتی|کرتی\s*ہوں|رہی\s*ہوں|آئی|لی|بتائی|'
    r'دیکھی|سنی|بولتی|پوچھتی|جاتی|آتی|سکتی|ہوتی|لگتی|'
    r'لے\s*گئی|کہا|پڑھا|سمجھی|مانگا)'
    r'|چاہتی\s*ہوں|کرتی\s*ہوں|رہی\s*ہوں|سکتی\s*ہوں'
    r')\b',
    re.UNICODE,
)

# Common Pakistani male name endings
_MALE_NAME_MARKERS = re.compile(
    r'\b(?:احمد|محمد|علی|حسین|حسن|عمر|عثمان|بلال|خالد|'
    r'اطہر|طلحہ|سعد|حمزہ|ذیشان|فواد|فیصل|عامر|'
    r'طاہر|ناصر|وحید|رشید|جمیل|کاشف|شاہد|'
    r'عدنان|اسلم|اکرم|انور|ارشد|اعجاز)\b',
    re.UNICODE,
)

_FEMALE_NAME_MARKERS = re.compile(
    r'\b(?:فاطمہ|عائشہ|مریم|زینب|خدیجہ|سلمیٰ|'
    r'سارہ|نادیہ|عصمت|رباب|ثروت|شاہدہ|'
    r'نسیم|ربعہ|نصرت|عظمیٰ|بلقیس|گوہر|'
    r'کنول|شمیم|خوارہ|عذرا|بشریٰ)\b',
    re.UNICODE,
)


def detect_caller_gender(text: str) -> str:
    """Detect caller gender from Urdu text. Returns 'male', 'female', or ''."""
    if _MALE_VERBS.search(text):
        return "male"
    if _FEMALE_VERBS.search(text):
        return "female"
    if _MALE_NAME_MARKERS.search(text):
        return "male"
    if _FEMALE_NAME_MARKERS.search(text):
        return "female"
    return ""


def is_farewell_response(text: str, lang: str = "ur") -> bool:
    if lang == "en":
        return bool(_EN_FAREWELL_RE.search(text))
    return bool(_FAREWELL_RE.search(text))


# ── Urdu gap word stripper ────────────────────────────────────────────────────
_GAP_RE = re.compile(
    r'^(جی\s+بالکل|بالکل|جی\s+ضرور|ضرور|جی\s+اچھا|اچھا|جی\s+ہاں|ہاں\s+جی|جی)\b[،,!!\s]*',
)


def strip_gap_words(text: str) -> str:
    while True:
        m = _GAP_RE.match(text)
        if not m:
            break
        text = text[m.end():]
    return text


# ── Urdu phonetic normaliser ──────────────────────────────────────────────────
_PHONETIC = [
    (re.compile(r'\bPSBA\b', re.IGNORECASE),                'پی ایس بی اے'),
    (re.compile(r'\bSahulat\s*Bazaar\b', re.IGNORECASE),    'سہولت بازار'),
    (re.compile(r'\bSahulat\b', re.IGNORECASE),             'سہولت'),
    (re.compile(r'\bBazaar\b', re.IGNORECASE),              'بازار'),
    (re.compile(r'براہِ\s*کرم'),  'پلیز'),
    (re.compile(r'براہ\s*کرم'),   'پلیز'),
    (re.compile(r'\bWhatsApp\b', re.IGNORECASE),   'واٹس ایپ'),
    (re.compile(r'\bwhatsapp\b', re.IGNORECASE),   'واٹس ایپ'),
    (re.compile(r'\bGoogle\b',   re.IGNORECASE),   'گوگل'),
    (re.compile(r'\bIATА?\b'),                      'آئی اے ٹی اے'),
    (re.compile(r'\bOK\b',       re.IGNORECASE),   'اوکے'),
    (re.compile(r'\bLahore\b',        re.IGNORECASE), 'لاہور'),
    (re.compile(r'\bPunjab\b',        re.IGNORECASE), 'پنجاب'),
    (re.compile(r'\bChina\s*Scheme\b', re.IGNORECASE), 'چائنا سکیم'),
    (re.compile(r'\bTownship\b',       re.IGNORECASE), 'ٹاؤن شپ'),
    (re.compile(r'\bHarbanspura\b',    re.IGNORECASE), 'ہربنس پورہ'),
    (re.compile(r'\bRaiwind\b',        re.IGNORECASE), 'رائے ونڈ'),
    (re.compile(r'\bThokar\b',         re.IGNORECASE), 'ٹھوکر'),
    (re.compile(r'\bChung\b',          re.IGNORECASE), 'چونگ'),
    (re.compile(r'\bSabzazaar\b',      re.IGNORECASE), 'سبزی منڈی'),
    (re.compile(r'\bJohar\s*Town\b',   re.IGNORECASE), 'جوہر ٹاؤن'),
    (re.compile(r'\bGulshan\s*Ravi\b', re.IGNORECASE), 'گلشن راوی'),
    (re.compile(r'\bAwan\s*Town\b',    re.IGNORECASE), 'اعوان ٹاؤن'),
    (re.compile(r'\bSher\s*Shah\b',    re.IGNORECASE), 'شیر شاہ'),
    (re.compile(r'\bWahdat\b',         re.IGNORECASE), 'وحدت'),
    (re.compile(r'\bMian\s*Plaza\b',   re.IGNORECASE), 'میاں پلازہ'),
    # Urdu-to-Urdu: slow down short 2-syllable names that TTS rushes
    (re.compile(r'چائنا'),  'چا\u200Cئے\u200Cنا'),
    (re.compile(r'میاں پلازہ'), 'مِ\u200Cیاں پ\u200Cلا\u200Cزہ'),
    (re.compile(r'میاں'),   'مِ\u200Cیاں'),
    (re.compile(r'\bvisa\b',     re.IGNORECASE),   'ویزا'),
    (re.compile(r'\btour\b',     re.IGNORECASE),   'ٹور'),
    (re.compile(r'\bpackage\b',  re.IGNORECASE),   'پیکج'),
    (re.compile(r'\bbooking\b',  re.IGNORECASE),   'بکنگ'),
    (re.compile(r'\bhotel\b',    re.IGNORECASE),   'ہوٹل'),
    (re.compile(r'\bspecialist\b', re.IGNORECASE), 'اسپیشلسٹ'),
    (re.compile(r'\bCustomer\s*Service\b', re.IGNORECASE), 'کسٹمر سروس'),
    (re.compile(r'\bdelivery\b',  re.IGNORECASE),  'ڈیلیوری'),
    (re.compile(r'\bhelpline\b',  re.IGNORECASE),  'ہیلپ لائن'),
    (re.compile(r'\b46\b'),    'چھیالیس'),
    (re.compile(r'\b36\b'),    'چھتیس'),
    (re.compile(r'\b35%\b'),   'پینتیس فیصد'),
    (re.compile(r'\b7%\b'),    'سات فیصد'),
    (re.compile(r'\b50%\b'),   'پچاس فیصد'),
    (re.compile(r'@'),                                  ' ایٹ '),
    (re.compile(r'\.pk\b', re.IGNORECASE),              ' ڈاٹ پی کے'),
    (re.compile(r'\.com\b', re.IGNORECASE),             ' ڈاٹ کام'),
    (re.compile(r'\.org\b', re.IGNORECASE),             ' ڈاٹ آرگ'),
    (re.compile(r'\.gov\b', re.IGNORECASE),             ' ڈاٹ گاو'),
    (re.compile(r'\.gop\b', re.IGNORECASE),             ' ڈاٹ جی او پی'),
    (re.compile(r'(?<=[a-z])\.(?=[a-z])', re.IGNORECASE), ' ڈاٹ '),
    (re.compile(r'(\d)-(\d)'),                           r'\1 \2'),
]


def urdu_phonetic(text: str) -> str:
    for pattern, replacement in _PHONETIC:
        text = pattern.sub(replacement, text)
    text = re.sub(r'\b\d{3,}\b', lambda m: ' '.join(m.group(0)), text)
    return text


# ── Urdu TTS text normalizer (for Uplift) ─────────────────────────────────
_URDU_DIGITS = {"0": "صفر", "1": "ایک", "2": "دو", "3": "تین", "4": "چار",
                "5": "پانچ", "6": "چھ", "7": "سات", "8": "آٹھ", "9": "نو"}

_EMAIL_RE = re.compile(r'([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})')
_PHONE_HYPHEN_RE = re.compile(r'\b(\d{3,4})-(\d{3,4})-?(\d{3,})?\b')
_LONG_DIGITS_RE = re.compile(r'\b\d{10,}\b')
_URL_RE = re.compile(r'https?://(?:www\.)?([a-zA-Z0-9.-]+)')

# ── Markdown strip ────────────────────────────────────────────────────────
_MD_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_MD_ITALIC_RE = re.compile(r'\*(.+?)\*')
_MD_HEADING_RE = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_LIST_DASH_RE = re.compile(r'^\s*[-*+]\s+', re.MULTILINE)
_MD_LIST_NUM_RE = re.compile(r'^\s*\d+[.)]\s+', re.MULTILINE)
_MD_TABLE_SEP_RE = re.compile(r'^\s*\|.+\|\s*$', re.MULTILINE)
_MD_TABLE_CONTENT_RE = re.compile(r'\|')


def _digits_to_urdu(m: re.Match) -> str:
    """Convert a digit sequence into Urdu digit names separated by spaces."""
    return " ".join(_URDU_DIGITS.get(ch, ch) for ch in m.group(0))


def normalize_tts_text(text: str) -> str:
    """Pre-process LLM output for Uplift TTS.

    Converts raw numbers, emails, and addresses into phonetic forms
    that Uplift TTS can pronounce correctly.
    """
    # 0. Strip markdown before anything else
    text = _MD_BOLD_RE.sub(r'\1', text)
    text = _MD_ITALIC_RE.sub(r'\1', text)
    text = _MD_HEADING_RE.sub('', text)
    text = _MD_LIST_DASH_RE.sub('', text)
    text = _MD_LIST_NUM_RE.sub('', text)
    text = _MD_TABLE_SEP_RE.sub('', text)
    text = _MD_TABLE_CONTENT_RE.sub('', text)
    text = text.strip()

    # 1. URLs → strip protocol
    text = _URL_RE.sub(r'\1', text)

    # 2. Email addresses → phonetic
    text = _EMAIL_RE.sub(r'\1 ایٹ \2', text)
    # Convert dots in domain to ڈاٹ (after @ or preceded by letter)
    text = re.sub(r'(?<=[a-zA-Z0-9])\.(?=[a-zA-Z]{2,})', ' ڈاٹ ', text)

    # 3. Phone numbers with hyphens: 0307-0002345 → digit-by-digit with pause
    def phone_replacer(m):
        parts = [p for p in m.groups() if p]
        return "۔ ".join(" ".join(_URDU_DIGITS.get(ch, ch) for ch in part) for part in parts)
    text = _PHONE_HYPHEN_RE.sub(phone_replacer, text)

    # 4. Long digit sequences (10+ digits) → digit-by-digit
    text = _LONG_DIGITS_RE.sub(_digits_to_urdu, text)

    # 5. Hyphen between digits → space (for split numbers)
    text = re.sub(r'(\d)-(\d)', r'\1 \2', text)

    return text
