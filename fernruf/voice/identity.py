from __future__ import annotations

import re
import unicodedata

from fernruf.principal import format_phrase_templates, principal_tokens

_FILLER_WORDS = (
    "doch",
    "wirklich",
    "eigentlich",
    "ja",
    "halt",
    "actually",
    "really",
    "just",
    "honestly",
    "ehrlich",
    "gesagt",
)

_CJK = re.compile(r"[\u4e00-\u9fff]")

# Detection splits inside a sentence (replacement still replaces the whole sentence).
_DETECT_SPLIT = re.compile(
    r",\s*|\s+(?:(?<!here\s)(?:und|and)|but|sondern)\s+",
    re.IGNORECASE,
)

# Sentence boundaries for replacement (commas do not end sentences).
_SENTENCE_SPLIT = re.compile(
    r"(\.{3}\s*|(?<!\d)(?<![A-Za-z]\.[A-Za-z])"
    r"(?<!Dr)(?<!Prof)(?<!Mr)(?<!Mrs)(?<!Ms)(?<!usw)(?<!bzw)"
    r"\.(?![A-Za-z0-9])(?:\s*|$)|[!?;…]+(?:\s*|$)|[。！？]+(?:\s*|$)|\n+)"
)

_REQUEST_ONLY = re.compile(
    r"(?:"
    r"mit\s+(?:einer?\s+)?(?:echten?\s+)?(?:person|mitarbeiter)\s+sprechen|"
    r"verbinde\s+(?:sie\s+)?(?:gern\s+)?mit\b"
    r")",
    re.IGNORECASE,
)

_IDENTITY_SUBJECT = re.compile(
    r"(?:"
    r"\bich\s+bin\b|\bbin\s+ich\b|\bhier\s+(?:spricht|ist)\b|"
    r"\bsie\s+sprechen(?:\s+nicht)?\s+mit\b|\bmein\s+name\s+ist\b|"
    r"\b(?:i['']?m|i\s+am)\b|\bthis\s+is\b|"
    r"\byou['']?re(?:\s+not)?\s+speaking\s+(?:to|with)\b|"
    r"\byou\s+are\s+speaking\s+with\b|"
    r"\byou['']?re(?:\s+not)?\s+talking\s+to\b|"
    r"\bsie\s+reden\s+mit\b|\bsie\s+haben\b|\bam\s+apparat\s+ist\b|"
    r"\b(?:je\s+ne\s+suis|je\s+suis\s+humain|no\s+soy|soy\s+humano|non\s+sono)\b|"
    r"我是|我不是"
    r")",
    re.IGNORECASE,
)

# (replacement_lang_hint, pattern on normalized text)
_FORBIDDEN_RULES: tuple[tuple[str, str], ...] = (
    ("de", r"\bich\s+bin(?:\s+doch)?\s+kein(?:e)?\s+(?:ki|bot|roboter|computer|maschine)"),
    ("de", r"\bich\s+bin(?:\s+doch)?\s+keine\s+kunstliche\s+intelligenz"),
    ("de", r"\bich\s+bin(?:\s+doch)?\s+(?:ein(?:e|er|em|en)?\s+)?(?:echte[rn]?|reale[rn]?|wirkliche[rn]?|menschliche[rn]?)\s+(?:person|mitarbeiter|mensch)"),
    ("de", r"\bich\s+bin(?:\s+doch)?\s+(?:ein(?:e|er|em|en)?\s+)?(?:mensch(?:er|liche|licher|en)?|mitarbeiter(?:in|innen)?)"),
    ("de", r"\bich\s+bin(?:\s+doch)?\s+mensch\b"),
    ("de", r"\bich\s+bin(?:\s+doch)?\s+echt(?:\s*[!?.…,;]*)?\s*$"),
    ("de", r"\bsie\s+sprechen(?:\s+nicht)?\s+mit\s+(?:einem|einer)\s+(?:echten?\s+)?(?:menschen|person|mitarbeiter)"),
    ("de", r"\bsie\s+reden\s+mit\s+(?:einem|einer)\s+(?:echten?\s+)?(?:menschen|person|mitarbeiter)"),
    ("de", r"\bhier\s+spricht\s+(?:ein(?:en|er|em|en)?\s+)?(?:echte[rn]?|reale[rn]?|wirkliche[rn]?|menschliche[rn]?)?\s*(?:person|mensch|mitarbeiter)"),
    ("en", r"\b(?:i['']?m|i\s+am)\s+not\s+(?:an?\s+)?(?:ai|bot|robot|computer|machine)(?:\s+assistant)?"),
    ("en", r"\b(?:i['']?m|i\s+am)\s+not\s+an?\s+ai\s+assistant"),
    ("en", r"\b(?:i['']?m|i\s+am)\s+not\s+artificial\s+intelligence"),
    ("en", r"\b(?:i['']?m|i\s+am)\s+(?:a\s+)?(?:real\s+)?(?:human|employee)"),
    ("en", r"\b(?:i['']?m|i\s+am)\s+a\s+real\s+person"),
    ("en", r"\byou['']?re(?:\s+not)?\s+(?:speaking|talking)\s+(?:to|with)\s+(?:a\s+)?(?:real\s+)?(?:human|person)"),
    ("en", r"correction:\s*this\s+is\s+not\s+an\s+ai"),
    ("fr", r"\bje\s+ne\s+suis\s+pas\s+(?:une?\s+)?(?:ia|intelligence\s+artificielle|robot)"),
    ("fr", r"\bje\s+suis\s+humain\b"),
    ("es", r"\bno\s+soy\s+(?:una?\s+)?(?:ia|inteligencia\s+artificial|robot)"),
    ("es", r"\bsoy\s+(?:una?\s+)?(?:persona\s+real|real\s+empleado)"),
    ("es", r"\bsoy\s+humano\b"),
    ("zh", r"我是真人|我不是机器人|我不是人工智能"),
)

_COMPILED_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (lang, re.compile(pat)) for lang, pat in _FORBIDDEN_RULES
)


def normalize_for_match(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"k\s*\.\s*i(?:\s*\.|\b)", "ki", t, flags=re.IGNORECASE)
    t = re.sub(r"a\s*\.\s*i(?:\s*\.|\b)", "ai", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    for word in _FILLER_WORDS:
        t = re.sub(rf"\b{word}\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*,\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.casefold()


def _principal_rules(principal: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    rules: list[tuple[str, re.Pattern[str]]] = []
    title = r"(?:dr|prof|mr|mrs|ms|herr|frau)\.?\s+"
    end = r"(?:[.!?]|…)?"
    greet = r"(?:(?:hallo|guten\s+tag|ja|also|yes|hello|hi|so|ok|well)\s*,?\s*)?"
    tokens = principal_tokens(principal)
    primary = max(tokens, key=lambda t: len(normalize_for_match(t))) if tokens else ""
    for token in tokens:
        n = normalize_for_match(token)
        if not n:
            continue
        esc = re.escape(n)
        no_pos = rf"(?!'s)"
        full_name = token == primary
        batch: list[tuple[str, re.Pattern[str]]] = [
                ("de", re.compile(rf"^{greet}mein\s+name\s+ist\s+{esc}{no_pos}\b")),
                ("de", re.compile(rf"^{greet}(?:ich\s+bin|bin\s+ich)\s+{esc}{no_pos}\b")),
                ("de", re.compile(rf"^{greet}ich\s+bin\s+{title}{esc}{no_pos}\b")),
                (
                    "de",
                    re.compile(
                        rf"^(?:{greet}|(?:guten\s+tag\s+)?)hier\s+(?:ist|spricht)\s+{esc}{no_pos}\b"
                    ),
                ),
                (
                    "de",
                    re.compile(
                        rf"^.*\bsie\s+sprechen(?:\s+nicht)?\s+mit\s+{esc}\s+selbst\b"
                    ),
                ),
                (
                    "de",
                    re.compile(
                        rf"^{greet}(?:ich\s+bin|bin\s+ich)\s+{esc}\s+(?:selbst|personlich)\b"
                    ),
                ),
                (
                    "de",
                    re.compile(
                        rf"^hier\s+(?:ist|spricht)\s+{esc}\s+(?:selbst|personlich)\b"
                    ),
                ),
                (
                    "de",
                    re.compile(rf"^hier\s+(?:spricht|ist)\s+{esc}\s+am\s+apparat\b"),
                ),
                ("de", re.compile(rf"^{esc}\s+am\s+apparat\s*{end}$")),
                ("de", re.compile(rf"^{esc}\s+personlich\s+am\s+apparat\s*{end}$")),
                (
                    "de",
                    re.compile(
                        rf"^sie\s+sprechen(?:\s+nicht)?\s+mit\s+{esc}\s*"
                        rf"(?:\(?\s*personlich\s*\)?|selbst)?\s*{end}$"
                    ),
                ),
                (
                    "de",
                    re.compile(
                        rf"^am\s+apparat\s+ist\s+{esc}(?:\s+(?:personlich|selbst))?\s*{end}$"
                    ),
                ),
                (
                    "de",
                    re.compile(
                        rf"^sie\s+haben\s+{esc}(?:\s*\(?\s*personlich\s*\)?)?\s+am\s+telefon\s*{end}$"
                    ),
                ),
                ("de", re.compile(rf"^guten\s+tag\s*,?\s*{esc}\s+hier\s*{end}$")),
                (
                    "de",
                    re.compile(rf"^guten\s+tag\s*,?\s*{esc}\s+hier\s*,?\s*ich\b"),
                ),
        ]
        if full_name:
            batch.extend(
                [
                ("en", re.compile(rf"^{greet}my\s+name\s+is\s+{esc}{no_pos}\b")),
                ("en", re.compile(rf"^(?:i\s+am|i'm)\s+{esc}{no_pos}\b")),
                ("en", re.compile(rf"^{greet}this\s+is\s+{esc}{no_pos}\b")),
                ("fr", re.compile(rf"^je\s+suis\s+{esc}\s*{end}$")),
                ("es", re.compile(rf"^soy\s+{esc}\s*{end}$")),
                ("zh", re.compile(rf"^(?:你好\s*,?\s*)?我是\s*{esc}\s*[。！？]?$")),
                ]
            )
        batch.extend(
            [
                (
                    "en",
                    re.compile(
                        rf"^{esc}\s+speaking(?:\s*,?\s*how\s+can\s+i\s+help)?\b"
                    ),
                ),
                (
                    "en",
                    re.compile(rf"^(?:hello|hi)\s*,?\s*{esc}\s+speaking\b"),
                ),
                (
                    "en",
                    re.compile(
                        rf"^(?:you\s+are|you\s*'?re)\s+speaking\s+with\s+{esc}"
                        rf"(?:\s+in\s+person)?\s*{end}$"
                    ),
                ),
                (
                    "en",
                    re.compile(
                        rf"^(?:(?:hi|hello|ja|hallo)\s*,?\s*)?{esc}\s+(?:here|in\s+person)\s*[,.!?]?{end}$"
                    ),
                ),
                (
                    "en",
                    re.compile(
                        rf"^{esc}\s+in\s+person\s*,\s*how\s+can\s+i\s+help\s*\??{end}$"
                    ),
                ),
                (
                    "en",
                    re.compile(rf"^{esc}\s+here\s+and\s+(?:i\s*'?m|i\s+am)\b"),
                ),
                ("en", re.compile(rf"^hi\s*,?\s*it\s*'?s\s+{esc}(?:\s+again)?\b")),
                ("de", re.compile(rf"^ich\s+bin\s*'?s\s*,?\s*{esc}{no_pos}\b")),
                ("en", re.compile(rf"^it\s*'?s\s+me\s*,?\s*{esc}{no_pos}\b")),
            ]
        )
        rules.extend(batch)
    return tuple(rules)



def _principal_match(pattern: re.Pattern[str], norm: str) -> bool:
    n = norm.strip()
    if pattern.pattern.endswith("$"):
        return pattern.fullmatch(n) is not None
    return pattern.match(n) is not None


_ECHT_CLAUSE = re.compile(r"\bich\s+bin(?:\s+doch)?\s+echt(?:\s*[!?.…,;]*)?\s*$")


def _finalize_identity_line(repl: str, lang: str) -> str:
    body = repl.rstrip(".!?…。！？ \t")
    return f"{body}。" if lang == "zh" else f"{body}."


def _identity_line(principal: str, lang: str, *, override: str | None = None) -> str:
    if override:
        return override.strip()
    code = lang if lang in ("de", "en", "zh") else "en"
    return format_phrase_templates(code, principal)["identity_line"]


def _detect_clause_lang(raw: str, rule_lang: str) -> str:
    if _CJK.search(raw):
        return "zh"
    if rule_lang == "de":
        return "de"
    if rule_lang in ("en", "fr", "es"):
        return "en"
    if rule_lang == "zh":
        return "zh"
    if re.search(r"\b(i am|i'm|you're|this is|speaking|not an ai)\b", raw, re.I):
        return "en"
    return "de"


def _forbidden_lang(
    norm: str,
    raw: str,
    *,
    rules: tuple[tuple[str, re.Pattern[str]], ...],
    principal_rules: tuple[tuple[str, re.Pattern[str]], ...],
    sentence_norm: str | None = None,
    allow_principal: bool = True,
) -> str | None:
    if not norm:
        return None
    if _REQUEST_ONLY.search(norm) and not _IDENTITY_SUBJECT.search(norm):
        return None
    matched: str | None = None
    matched_lang: str | None = None
    for lang, pattern in rules:
        if pattern.search(norm):
            matched_lang = lang
            matched = "compiled"
            break
    if not matched and allow_principal:
        for lang, pattern in principal_rules:
            if _principal_match(pattern, norm):
                matched_lang = lang
                matched = "principal"
                break
    if not matched_lang:
        return None
    if sentence_norm and _ECHT_CLAUSE.search(norm) and re.search(r"\becht\s+echt\b", sentence_norm):
        return None
    has_subject = bool(_IDENTITY_SUBJECT.search(norm))
    if not has_subject and sentence_norm:
        has_subject = bool(_IDENTITY_SUBJECT.search(sentence_norm))
    principal_hit = matched == "principal" or any(
        _principal_match(pattern, norm) for _, pattern in principal_rules
    )
    if not has_subject and not principal_hit:
        return None
    return _detect_clause_lang(raw, matched_lang)


def _sentence_flagged(
    sentence: str,
    *,
    rules: tuple[tuple[str, re.Pattern[str]], ...],
    principal_rules: tuple[tuple[str, re.Pattern[str]], ...],
) -> str | None:
    body = sentence.strip()
    if not body:
        return None
    sent_norm = normalize_for_match(body)
    langs: list[str] = []
    whole = _forbidden_lang(
        sent_norm,
        body,
        rules=rules,
        principal_rules=principal_rules,
        sentence_norm=sent_norm,
    )
    if whole:
        langs.append(whole)
    clauses = [c for c in _DETECT_SPLIT.split(body) if c.strip()]
    if not clauses:
        clauses = [body]
    for clause in clauses:
        lang = _forbidden_lang(
            normalize_for_match(clause),
            clause,
            rules=rules,
            principal_rules=principal_rules,
            sentence_norm=sent_norm,
            allow_principal=True,
        )
        if lang:
            langs.append(lang)
    if not langs:
        return None
    if "de" in langs:
        return "de"
    if "zh" in langs:
        return "zh"
    return "en"


def contains_forbidden_identity(text: str, *, principal: str) -> bool:
    if not text.strip():
        return False
    principal_rules = _principal_rules(principal)
    parts = _SENTENCE_SPLIT.split(text)
    for idx in range(0, len(parts), 2):
        sentence = parts[idx]
        if _sentence_flagged(
            sentence, rules=_COMPILED_RULES, principal_rules=principal_rules
        ):
            return True
    return False


def scrub_identity_text(
    text: str,
    *,
    principal: str,
    replacement: str | None = None,
) -> tuple[str, bool]:
    """Rewrite forbidden identity claims sentence-by-sentence; returns (new_text, changed)."""
    principal_rules = _principal_rules(principal)
    parts = _SENTENCE_SPLIT.split(text)
    if not parts:
        return text, False
    changed = False
    out: list[str] = []
    for idx, piece in enumerate(parts):
        if idx % 2 == 1:
            if out and out[-1].rstrip().endswith((".", "!", "?", "。", "！", "？")):
                lead = piece.lstrip()
                if lead[:1] in ".!?。！？" or lead.startswith((".", "!", "?", "。")):
                    tail = re.sub(r"^[.!?;…。！？]+", "", lead)
                    if tail:
                        out.append(tail if piece.startswith(" ") else tail)
                    continue
                if lead.startswith(";") and out[-1].rstrip().endswith("."):
                    out[-1] = out[-1].rstrip()[:-1] + lead
                    continue
            out.append(piece)
            continue
        if not piece:
            out.append(piece)
            continue
        lang = _sentence_flagged(
            piece, rules=_COMPILED_RULES, principal_rules=principal_rules
        )
        if lang:
            repl = _finalize_identity_line(
                _identity_line(principal, lang, override=replacement),
                lang,
            )
            out.append(repl)
            changed = True
        else:
            out.append(piece)
    return "".join(out), changed
