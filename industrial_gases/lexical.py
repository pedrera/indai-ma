"""Pure lexical helpers shared by deterministic and LLM grounding paths."""
from decimal import Decimal, InvalidOperation
import re


_NUMBER = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:[ .]\d{3})+|\d+(?:[.,]\d+)?)(?![\w])"
)
_UNIT = re.compile(
    r"(?:Nm3\s*\u00b3|Sm3\s*\u00b3|m3\s*\u00b3|"
    r"Nm\s*\u00b3|Sm\s*\u00b3|m\s*\u00b3|Nm3|Sm3|m3|kg|t|L)(?=\s|[.,;]|$)",
    re.IGNORECASE,
)
_WORD_UNIT = re.compile(
    r"(?:kilos?|kilogramos?|toneladas?|litros?|Nm3\s*\u00b3|Sm3\s*\u00b3|m3\s*\u00b3|"
    r"Nm\s*\u00b3|Sm\s*\u00b3|"
    r"m\s*\u00b3|Nm3|Sm3|m3|kg|t|L)(?=\s|[.,;]|$)", re.IGNORECASE
)


def parse_decimal_number(raw: str) -> Decimal:
    """Parse the established Spanish-friendly numeric formats exactly."""
    value = raw.replace("\u00a0", " ").replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid numeric lexeme") from error
    if not result.is_finite():
        raise ValueError("numeric lexeme must be finite")
    return result


def numeric_lexemes(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _NUMBER.finditer(text))


def normalize_unit_lexeme(raw: str, *, include_words: bool = False) -> str:
    """Normalize written unit spellings to catalog codes; never convert."""
    token = raw.strip()
    aliases = {
        "m\u00b3": "m3", "m \u00b3": "m3", "Nm\u00b3": "Nm3",
        "nm\u00b3": "Nm3", "Nm \u00b3": "Nm3", "nm \u00b3": "Nm3",
        "Sm\u00b3": "Sm3", "sm\u00b3": "Sm3", "Sm \u00b3": "Sm3", "sm \u00b3": "Sm3",
        "m3\u00b3": "m3", "Nm3\u00b3": "Nm3", "Sm3\u00b3": "Sm3",
        "nm3\u00b3": "Nm3", "sm3\u00b3": "Sm3",
    }
    if token in {"kg", "t", "L", "m3", "Nm3", "Sm3"}:
        return token
    if token in aliases:
        return aliases[token]
    if include_words:
        words = {
            "kilo": "kg", "kilos": "kg", "kilogramo": "kg",
            "kilogramos": "kg", "tonelada": "t",
            "toneladas": "t", "litro": "L", "litros": "L",
        }
        return words.get(token.casefold(), token)
    return token


def unit_after_number(text: str, *, include_words: bool = False) -> tuple[str, str] | None:
    """Return one numeric lexeme and directly following unit, if unambiguous."""
    unit_pattern = _WORD_UNIT if include_words else _UNIT
    matches = []
    for number_match in _NUMBER.finditer(text):
        unit_match = re.match(rf"\s*({unit_pattern.pattern})", text[number_match.end():], re.IGNORECASE)
        if unit_match:
            matches.append((number_match.group(0), normalize_unit_lexeme(
                unit_match.group(1), include_words=include_words)))
    return matches[0] if len(matches) == 1 else None


def relative_days_evidence(text: str) -> int | None:
    match = re.fullmatch(r"\s*dentro\s+de\s+(\d+)\s+d[i\u00ed]as\s*", text, re.IGNORECASE)
    return int(match.group(1)) if match else None
