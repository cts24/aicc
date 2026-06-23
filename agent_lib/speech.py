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
