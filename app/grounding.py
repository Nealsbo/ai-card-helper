"""Conservative source-context checks; statements are never reduced to bare tokens."""

import re


def normalized(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        text.strip()
        .casefold()
        .replace("ё", "е")
        .replace(",", ".")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-"),
    )


def literal_present(value: str, text: str) -> bool:
    value = normalized(value).rstrip(".;:")
    return bool(
        value and re.search(r"(?<![\w.,])" + re.escape(value) + r"(?!\w|[.,]\d)", normalized(text))
    )


UNKNOWN = re.compile(
    r"не\s+указ\w*|неизвест\w*|нет\s+(?:данных|сведений|информации)|"
    r"(?:данных|сведений|информации)\s+нет|"
    r"(?:данные|сведения|информация)\b[^.!?;]{0,160}\bотсутств\w*|"
    r"not\s+(?:specified|known|provided)|no\s+(?:data|information)|unknown",
    re.I,
)
CONDITIONAL = re.compile(
    r"только\s+при|при\s+условии|\bесли\b|only\s+(?:if|when)|provided\s+that|in\s+case", re.I
)
NEGATIVE = re.compile(
    r"не\s+(?:поддерж\w*|предназнач\w*|совместим\w*|име\w*|оснащ\w*|предусмотр\w*|допуска\w*)|"
    r"без\s+(?:интерфейс\w*|поддерж\w*|возможност\w*)|отсутств\w*|"
    r"(?:does|do|is|are)\s+not\s+(?:support\w*|compatible|equipped|intended|available)|"
    r"not\s+(?:supported|compatible|intended)|unsupported|without\s+(?:support|interface)",
    re.I,
)
INSTRUCTION = re.compile(
    r"ignore\s+(?:all|previous|the\s+above)\s+(?:rules|instructions)|"
    r"игнорир\w*\s+(?:все|предыдущ\w*)\s+(?:правил\w*|инструкц\w*)|"
    r"system\s+prompt|системн\w*\s+промпт",
    re.I,
)


def context_kind(text: str) -> str:
    if UNKNOWN.search(text):
        return "unknown"
    if CONDITIONAL.search(text):
        return "condition"
    if NEGATIVE.search(text):
        return "restriction"
    return "statement"


def clauses(text: str) -> list[str]:
    return re.split(r";\s*|,\s+(?!\d)|\s+(?:но|but)\s+", text, flags=re.I)


FAMILIES = (
    (r"диапазон|предел|range", r"диапазон|предел|range"),
    (r"диаметр|diameter", r"диаметр|diameter"),
    (r"класс.*точност|accuracy\s*class", r"класс.*точност|accuracy\s*class"),
    (r"погрешност|uncertainty|error", r"погрешност|uncertainty|error"),
    (r"точност|accuracy", r"точност|accuracy"),
    (r"присоедин|резьб|thread|connection", r"присоедин|резьб|thread|connection"),
    (r"интерфейс|interface", r"интерфейс|interface|поддерж\w*|support\w*"),
    (r"располож|orientation|position", r"располож|orientation|position"),
    (r"материал|material", r"материал|material"),
    (r"питани|напряжени|voltage|supply", r"питани|напряжени|voltage|supply"),
)


def parameter_supported(label: str, evidence: str) -> bool:
    for label_pattern, text_pattern in FAMILIES:
        if re.search(label_pattern, label, re.I):
            return bool(re.search(text_pattern, evidence, re.I))
    return normalized(label).rstrip(".;:") in normalized(evidence)


def explicit_parameter(text: str) -> tuple[str, str] | None:
    match = re.match(r"^([^:：\n]{1,100})[:：]\s*(.+)$", text.strip(), re.S)
    if not match:
        return None
    label, value = match.groups()
    return label.strip(), value.strip().rstrip(".;")
