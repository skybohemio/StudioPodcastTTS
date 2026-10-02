"""
Formateur de texte selon les bonnes pratiques officielles Voxtral TTS :

- **Verbalizable form** : nombres, symboles, monnaies, pourcentages → en toutes lettres
- **No rich formatting** : suppression du markdown, des emojis et caractères spéciaux
- **Abbreviations** : FBI → F-B-I (sauf acronymes prononçables : NASA, ONU…)
- **Pas de balises** : Voxtral lit tout le texte tel quel ; les indications de jeu
  « (rire) », « (ton grave) »… sont retirées avant synthèse (strip_stage_directions)
- **Length** : le découpage en blocs ≤ 300 mots est géré par voxtral.split_script
"""
import re

from num2words import num2words

# acronymes prononcés comme des mots (ne pas épeler)
SPELLABLE_WHITELIST = {
    "NASA", "ONU", "OTAN", "UNESCO", "UNICEF", "AIDS", "SIDA", "OPEC", "OPEP",
    "LASER", "RADAR", "SONAR", "GIF", "SIM", "PIN", "RAID", "FNAC", "OVNI",
    "COVID", "ENA", "INSEE", "NATO",
}
MAX_SPELLED_LEN = 5  # au-delà, un mot en capitales est un mot crié (ATTENTION), pas un sigle

CURRENCY = {
    "€": {"fr": "euros", "en": "euros", "de": "Euro", "es": "euros", "nl": "euro",
          "pt": "euros", "it": "euro", "hi": "यूरो", "ar": "يورو"},
    "$": {"fr": "dollars", "en": "dollars", "de": "Dollar", "es": "dólares",
          "nl": "dollar", "pt": "dólares", "it": "dollari"},
    "£": {"fr": "livres sterling", "en": "pounds", "de": "Pfund", "es": "libras",
          "nl": "pond", "pt": "libras", "it": "sterline"},
}
PERCENT = {"fr": "pour cent", "en": "percent", "de": "Prozent", "es": "por ciento",
           "nl": "procent", "pt": "por cento", "it": "per cento", "hi": "प्रतिशत", "ar": "بالمئة"}
DECIMAL_WORD = {"fr": "virgule", "en": "point", "de": "Komma", "es": "coma",
                "nl": "komma", "pt": "vírgula", "it": "virgola"}
# séparateur décimal usuel par langue (l'autre sert de séparateur de milliers)
DECIMAL_SEP = {"en": ".", "hi": ".", "ar": "."}

_N2W_LANGS = {"fr", "en", "de", "es", "nl", "pt", "it", "ar"}

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F]+")

_THIN_SPACES = " \u00a0\u202f"
_ROMAN_RE = r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
# sigles qui ressemblent à des chiffres romains, et mots qui ne sont pas des noms de souverain
_ROMAN_LIKE_ACRONYMS = {"CD", "CV", "DC", "MC", "CM", "MD", "MI", "DI", "LI", "CC", "MIX", "CIV"}
_NOT_NAMES = {"le", "la", "les", "un", "une", "des", "du", "de", "ce", "cet", "cette", "mon",
              "ton", "son", "ma", "ta", "sa", "nos", "vos", "leur", "the", "a", "an", "my",
              "your", "his", "her", "our", "their", "this", "that", "et", "ou", "and", "or"}
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# indications de jeu courantes (FR/EN) que Voxtral lirait à voix haute
_PAUSE_WORDS = r"(?:long(?:ue)?\s+)?(?:pause|silence|temps)(?:\s+long(?:ue)?)?"
_DIRECTION_WORDS = (
    r"silence|pause|transition|rire|rires|rit|sourire|souriant|soupir|soupire|chuchot|murmur|voix|ton\b|"
    r"avec\s|enthousias|ironique|dramatique|grave|ému|émue|tendre|amus|"
    r"laugh|sigh|whisper|chuckle|excited|sad|happy|softly|slowly|tone|voice|"
    r"musique|jingle|bruitage|applaudis|inspir|respir|toux|hésit|un\s+temps"
)


def _lang_num2words(lang):
    return lang if lang in _N2W_LANGS else None  # hindi : non géré par num2words


def _n2w(n, lang, **kw):
    code = _lang_num2words(lang)
    if code is None:
        return str(n)
    return num2words(n, lang=code, **kw).replace(",", "")


def _roman_to_int(s):
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_VALUES[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def strip_rich_formatting(text):
    """Supprime markdown, emojis, liens, caractères spéciaux non prononçables."""
    t = text
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)        # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)          # liens [txt](url)
    t = re.sub(r"https?://\S+", "", t)                       # URL nues
    t = re.sub(r"`{1,3}[^`]*`{1,3}", "", t)                  # code inline/blocs
    t = re.sub(r"[*_~#>|]+", "", t)                          # marqueurs markdown
    t = re.sub(r"^\s*[-•●]\s+", "", t, flags=re.M)           # puces
    t = _EMOJI_RE.sub("", t)                                  # emojis
    t = re.sub(r"[©®™§†‡★☆✓✔✗✘♪♫]", " ", t)                 # symboles divers
    t = t.replace("…", "...")                                 # garde les silences
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def strip_stage_directions(text):
    """Retire les indications de jeu entre parenthèses/crochets (Voxtral ne les
    interprète pas : il les lirait). Les pauses deviennent des points de suspension.
    Renvoie (texte, nombre d'indications retirées)."""
    count = 0

    def pause(m):
        nonlocal count
        count += 1
        return " ... "

    def drop(m):
        nonlocal count
        count += 1
        return " "

    # crochets : toujours des indications scéniques ([Pause, silence dramatique], [Musique]…)
    t = re.sub(r"\[[^\]\n]{0,80}\]",
               lambda m: pause(m) if re.search(r"pause|silence", m.group(0), re.I) else drop(m), text)
    t = re.sub(r"[(\[]\s*" + _PAUSE_WORDS + r"\s*[)\]]", pause, t, flags=re.I)
    t = re.sub(r"[(\[]\s*(?:" + _DIRECTION_WORDS + r")[^()\[\]\n]{0,40}[)\]]", drop, t, flags=re.I)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" +([,.])", r"\1", t)
    t = re.sub(r"(\.\.\.\s*)+\.*", "... ", t)
    t = re.sub(r"\.\.\. ([,.!?;:])", r"...\1", t)
    # une pause en tout début de réplique ne sert à rien
    t = re.sub(r"^([ \t]*(?:Speaker\s*\d+\s*:)?)[ \t]*\.\.\.[ \t]*", r"\1 ", t, flags=re.M | re.I)
    t = re.sub(r"^ +| +$", "", t, flags=re.M)
    return t, count


def _parse_number(s, lang):
    """'3 000' / '1.000.000,50' / '12,50' → (partie entière:int, décimales:str|None)."""
    dec = DECIMAL_SEP.get(lang, ",")
    s = re.sub("[" + _THIN_SPACES + "]", "", s)
    seps = re.findall(r"[.,]", s)
    if not seps:
        return int(s), None
    if len(set(seps)) == 2:          # 1.000.000,50 : le dernier séparateur est décimal
        d = seps[-1]
        ip, fp = s.rsplit(d, 1)
        return int(re.sub(r"[.,]", "", ip)), fp
    c = seps[0]
    if len(seps) > 1:                # 1.000.000 : milliers
        return int(s.replace(c, "")), None
    ip, fp = s.split(c)
    if c != dec and len(fp) == 3:    # 1.000 (fr) / 1,000 (en) : milliers
        return int(ip + fp), None
    return int(ip), fp


def _say_number(s, lang):
    ip, fp = _parse_number(s, lang)
    if lang == "en" and fp is None and 1100 <= ip <= 2099 and len(s) == 4:
        return _n2w(ip, lang, to="year")
    words = _n2w(ip, lang)
    if fp:
        zeros = len(fp) - len(fp.lstrip("0"))
        rest = fp.lstrip("0")
        parts = [_n2w(0, lang)] * zeros + ([_n2w(int(rest), lang)] if rest else [])
        words += " " + DECIMAL_WORD.get(lang, "point") + " " + " ".join(parts)
    return words


def _say_amount(s, word, lang):
    ip, fp = _parse_number(s, lang)
    out = _n2w(ip, lang)
    if lang == "fr" and re.search(r"(million|milliard)s?$", out):
        out += " d'" if word[0] in "aeiouy" else " de "  # un million d'euros
        out += word
    else:
        out += " " + word
    if fp and int(fp):
        out += " " + _n2w(int(fp.ljust(2, "0")[:2]), lang)
    return out


# nombre : milliers groupés (espaces / points / virgules) ou simple, décimales optionnelles
_NUM = (r"(?<![\d.,])(?:\d{1,3}(?:[" + _THIN_SPACES + r"]\d{3})+|\d{1,3}(?:[.,]\d{3}){2,}|"
        r"\d+)(?:[.,]\d+)?(?![\d])")


def verbalize_numbers(text, lang="fr"):
    """Nombres, monnaies, pourcentages, ordinaux, heures → forme parlée."""
    if _lang_num2words(lang) is None:
        return text
    t = text

    # monnaies : 10€ / $10 / 12,50 €
    for sym, names in CURRENCY.items():
        word = names.get(lang, names.get("en", sym))
        t = re.sub("(" + _NUM + r")\s*" + re.escape(sym),
                   lambda m: _say_amount(m.group(1), word, lang), t)
        t = re.sub(re.escape(sym) + r"\s*(" + _NUM + ")",
                   lambda m: _say_amount(m.group(1), word, lang), t)

    # pourcentages : 50 %
    pw = PERCENT.get(lang, PERCENT["en"])
    t = re.sub("(" + _NUM + r")\s*%", lambda m: _say_number(m.group(1), lang) + " " + pw, t)

    if lang == "fr":
        # heures : 14h30, 9 h, 18h05
        def hours(m):
            h = int(m.group(1))
            out = ("une" if h == 1 else _n2w(h, lang)) + (" heure" if h <= 1 else " heures")
            if m.group(2):
                out += " " + _n2w(int(m.group(2)), lang)
            return out
        t = re.sub(r"\b(\d{1,2})\s?h\s?(\d{2})?\b", hours, t)
        # ordinaux : 1er, 1re, 2e, 3ème, XVIIIe, Ier
        def ordinal(n, suffix):
            if n == 1:
                return "première" if suffix.startswith(("r", "è")) else "premier"
            return _n2w(n, lang, to="ordinal")
        t = re.sub(r"\b(\d+)(er|re|ère|e|ème|eme)\b",
                   lambda m: ordinal(int(m.group(1)), m.group(2)), t)
        def roman_ordinal(m):
            r = m.group(1)
            # « Le », « Ce », « De », « Me » ne sont pas des ordinaux romains
            if not re.fullmatch(_ROMAN_RE, r) or (len(r) == 1 and r not in "IVX"):
                return m.group(0)
            return ordinal(_roman_to_int(r), m.group(2))
        t = re.sub(r"\b([IVXLCDM]+)(er|re|ère|e|ème|eme)\b", roman_ordinal, t)
    elif lang == "en":
        t = re.sub(r"\b(\d+)(st|nd|rd|th)\b",
                   lambda m: _n2w(int(m.group(1)), lang, to="ordinal"), t)

    # souverains / papes : Louis XIV, Henri IV, Jean-Paul II (au moins 2 lettres romaines)
    def regnal(m):
        name, r = m.group(1), m.group(2)
        if name.strip().lower() in _NOT_NAMES or r in _ROMAN_LIKE_ACRONYMS \
                or not re.fullmatch(_ROMAN_RE, r):
            return m.group(0)
        n = _roman_to_int(r)
        if lang == "en":
            return name + "the " + _n2w(n, lang, to="ordinal")
        return name + _n2w(n, lang)
    t = re.sub(r"(\b[A-ZÀ-Ý][a-zà-ÿ]+(?:-[A-ZÀ-Ý][a-zà-ÿ]+)? )([IVXLCDM]{2,})\b", regnal, t)

    t = re.sub(_NUM, lambda m: _say_number(m.group(0), lang), t)
    return t


def expand_abbreviations(text):
    """FBI → F-B-I (sigles en capitales), sauf acronymes prononcés comme des mots ;
    les mots longs en capitales (ATTENTION) sont simplement remis en minuscules."""
    def repl(m):
        tok = m.group(0)
        if tok in SPELLABLE_WHITELIST or len(tok) > MAX_SPELLED_LEN:
            return tok.capitalize()
        return "-".join(tok)
    return re.sub(r"\b[A-ZÀ-ÖØ-Þ]{2,}\b", repl, text)


def _protect_key(i):
    """Clé de protection en lettres minuscules uniquement (aucun chiffre ni symbole
    que le formatage pourrait modifier), unique quel que soit le nombre de balises."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return f"\x00spk{s}\x00"


SPEAKER_RE = re.compile(r"Speaker\s*(\d+)\s*:", re.I)


def format_text(text, lang="fr"):
    """Pipeline complet : nettoyage → nombres → abréviations → espaces.
    Les balises de locuteur (« Speaker 1: »…) sont protégées du formatage."""
    tags = {}

    def protect(m):
        key = _protect_key(len(tags))
        tags[key] = f"Speaker {m.group(1)}:"
        return key
    t = SPEAKER_RE.sub(protect, text)
    t = strip_rich_formatting(t)
    t = verbalize_numbers(t, lang)
    t = expand_abbreviations(t)
    for key, tag in tags.items():
        t = t.replace(key, tag)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return t.strip()
