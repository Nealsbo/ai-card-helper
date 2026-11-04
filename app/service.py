import re
import time
from collections import defaultdict

from app.llm import InvalidOutput, LLMError, LLMClient
from app.models import (
    Characteristic,
    Conflict,
    Extraction,
    Fact,
    SeoLimits,
    GenerationResult,
    ProductCard,
    ProductInput,
)
from app.prompts import COPY_SYSTEM, EXTRACT_SYSTEM
from app.composer import build_palette
from app.grounding import (
    INSTRUCTION,
    clauses,
    context_kind,
    explicit_parameter,
    literal_present,
    parameter_supported,
)


def canonical(text: str) -> str:
    return re.sub(
        r"\s+",
        "",
        text.rstrip(" .;:")
        .casefold()
        .replace("ё", "е")
        .replace(",", ".")
        .replace("–", "-")
        .replace("−", "-")
        .replace("—", "-"),
    )


PROFILES = [
    (
        ("манометр", "давлен"),
        {
            "Диапазон измерений": ("диапазон", "предел"),
            "Точность": ("точност", "погрешност"),
            "Присоединение": ("присоедин", "резьб"),
            "Диаметр корпуса": ("диаметр",),
        },
    ),
    (
        ("термометр", "термопреобразователь", "температур"),
        {
            "Диапазон измерений": ("диапазон", "предел"),
            "Точность": ("точност", "погрешност"),
            "Исполнение / установка": ("исполнен", "установ", "монтаж", "присоедин"),
        },
    ),
]


def expected_fields(name: str) -> dict[str, tuple[str, ...]]:
    for keywords, fields in PROFILES:
        if any(word in name.casefold() for word in keywords):
            return fields
    return {"Диапазон измерений": ("диапазон", "предел"), "Точность": ("точност", "погрешност")}


def no_data(value: str | None) -> bool:
    return not value or canonical(value) in {
        "данныхнет",
        "нетданных",
        "неизвестно",
        "неуказано",
        "неуказан",
        "неуказана",
        "-",
    }


def source_segments(description: str) -> dict[str, str]:
    """Give exact input fragments stable IDs; the LLM need not reproduce quotations."""
    parts = [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\n+", description) if part.strip()]
    return {f"S{i}": part for i, part in enumerate(parts, 1)}


def build_facts(product: ProductInput, extraction: Extraction):
    """Accept only verbatim supported extraction; quarantine conflicting groups."""
    groups = defaultdict(list)
    sources = source_segments(product.description)
    empty_specs = []
    for label, value in product.specs.items():
        if not no_data(value):
            groups[canonical(label)].append(
                Fact(id="", label=label, value=value, source="specs", evidence=f"{label}: {value}")
            )
        else:
            empty_specs.append(label)
    # Validate references even when the source will later be quarantined.
    for item in extraction.facts:
        if item.source_id not in sources:
            raise InvalidOutput(f"Неизвестный source_id: {item.source_id}")
        if item.spec_key is not None:
            if item.spec_key not in product.specs:
                raise InvalidOutput("Извлечение ссылается на неизвестный ключ specs")
            if item.value not in sources[item.source_id]:
                raise InvalidOutput("value должен быть точной подстрокой источника")

    quarantined = set()
    contexts = {}
    # Inspect every source for conflicts, including sources omitted by extraction.
    for source_id, evidence in sources.items():
        if INSTRUCTION.search(evidence):
            continue
        items = [item for item in extraction.facts if item.source_id == source_id]
        kind = context_kind(evidence)
        if kind == "unknown":
            continue
        for label, original in product.specs.items():
            if no_data(original):
                continue
            relevant = [part for part in clauses(evidence) if parameter_supported(label, part)]
            mapped = any(item.spec_key == label for item in items)
            for part in relevant:
                part_kind = context_kind(part)
                explicit = explicit_parameter(part)
                if part_kind == "unknown":
                    continue
                negative = part_kind in {"restriction", "condition"}
                same = literal_present(original, part)
                # A generic threaded connection does not disprove a specific thread.
                different = explicit is not None or (mapped and re.search(r"\d", part) is not None)
                absent_property = canonical(label) in canonical(part) and part_kind == "restriction"
                if (negative and (same or absent_property)) or (different and not same):
                    value = explicit[1] if explicit else part.strip()
                    groups[canonical(label)].append(
                        Fact(
                            id="",
                            label=label,
                            value=value,
                            source="description",
                            evidence=evidence,
                            kind="parameter",
                        )
                    )
                    quarantined.add(evidence)
        if items or kind in {"restriction", "condition"}:
            # Labels and bare values proposed by the model never become parameters.
            # Only exact source-labelled pairs may enter the parameter table.
            explicit = explicit_parameter(evidence)
            mapped_label = next((item.spec_key for item in items if item.spec_key), None)
            if (
                explicit
                and kind == "statement"
                and (
                    not mapped_label
                    or (
                        no_data(product.specs[mapped_label])
                        and parameter_supported(mapped_label, explicit[0])
                    )
                )
            ):
                label, value = explicit
                label = mapped_label or label
                groups[canonical(label)].append(
                    Fact(
                        id="",
                        label=label,
                        value=value,
                        source="description",
                        evidence=evidence,
                        kind="parameter",
                    )
                )
            else:
                contexts[source_id] = Fact(
                    id="",
                    label="Сведения из описания",
                    value=evidence,
                    source="description_quote",
                    evidence=evidence,
                    kind=kind,
                )

    facts, conflicts, characteristics = [], [], []
    for group in groups.values():
        distinct = {canonical(f.value) for f in group}
        first = group[0]
        if len(distinct) > 1:
            conflicts.append(
                Conflict(
                    field=first.label,
                    values=list(dict.fromkeys(f.value for f in group)),
                    evidence=list(dict.fromkeys(f.evidence for f in group)),
                )
            )
            characteristics.append(
                Characteristic(
                    label=first.label,
                    value="Требует уточнения: противоречие в источниках",
                    source="conflict",
                    evidence=" | ".join(f.evidence for f in group),
                )
            )
        else:
            if first.evidence in quarantined:
                continue
            first.id = f"F{len(facts) + 1}"
            facts.append(first)
            characteristics.append(
                Characteristic(
                    label=first.label,
                    value=first.value,
                    source=first.source,
                    evidence=first.evidence,
                )
            )

    for fact in contexts.values():
        if fact.evidence not in quarantined:
            fact.id = f"F{len(facts) + 1}"
            facts.append(fact)

    missing = []
    if no_data(product.manufacturer):
        missing.append("Производитель")
    if not product.description:
        missing.append("Описание / фрагмент паспорта")
    if not any(not no_data(v) for v in product.specs.values()) and not facts:
        missing.append("Технические характеристики")
    present_labels = [canonical(f.label) for group in groups.values() for f in group]
    for label in empty_specs:
        if canonical(label) not in groups:
            missing.append(label)
            characteristics.append(
                Characteristic(label=label, value="данных нет", source="missing", evidence="")
            )
    for label, aliases in expected_fields(product.name).items():
        if not any(any(alias in present for alias in aliases) for present in present_labels):
            # Avoid duplicating a missing parameter already explicitly supplied empty.
            if not any(any(alias in canonical(item) for alias in aliases) for item in missing):
                missing.append(label)
                characteristics.append(
                    Characteristic(label=label, value="данных нет", source="missing", evidence="")
                )
    return facts, conflicts, characteristics, list(dict.fromkeys(missing))


def markdown_card(card: ProductCard) -> str:
    def safe(value: str):
        # Plain text export; prevent source text from injecting HTML/headings/tables.
        value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value).replace("\n", " ")

    lines = [
        f"# {safe(card.seo_title)}",
        "",
        "## SEO-описание",
        "",
        safe(card.seo_description),
        "",
        "## Характеристики",
        "",
        "| Параметр | Значение |",
        "| --- | --- |",
    ]
    lines += [f"| {safe(f.label)} | {safe(f.value)} |" for f in card.characteristics]
    lines += ["", "## Поисковые запросы", ""]
    lines += [f"- {safe(q)}" for q in card.search_queries]
    lines += ["", "## Для менеджера", "", safe(card.commercial_block)]
    if card.missing_fields:
        lines += ["", "## Недостающие данные", ""]
        lines += [f"- {safe(f)}: данных нет" for f in card.missing_fields]
    if card.conflicts:
        lines += ["", "## Требуют уточнения", ""]
        lines += [
            f"- {safe(c.field)}: {' / '.join(safe(v) for v in c.values)}" for c in card.conflicts
        ]
    lines += ["", "## Предупреждения", ""]
    lines += [f"- {safe(w)}" for w in card.warnings]
    return "\n".join(lines) + "\n"


async def generate(
    product: ProductInput, llm: LLMClient, limits: SeoLimits | None = None
) -> GenerationResult:
    limits = limits or llm.settings.seo_limits
    started = time.monotonic()
    extraction_payload = {
        "name": product.name,
        "spec_keys": list(product.specs),
        "sources": [
            {"id": key, "text": text} for key, text in source_segments(product.description).items()
        ],
    }
    # Validate evidence and structure before any extracted fact reaches the copy writer.
    for attempt in range(2):
        try:
            extraction = (
                await llm.complete(EXTRACT_SYSTEM, extraction_payload, Extraction)
                if product.description
                else Extraction(facts=[])
            )
            facts, conflicts, characteristics, missing = build_facts(product, extraction)
            palette = build_palette(product, facts, missing, conflicts)
            try:
                palette.check_feasible(limits, missing)
            except LLMError as exc:
                # A small model can omit useful statements on its first pass. Retry
                # extraction once if the input itself could supply enough text.
                represented = {fact.evidence for fact in facts}
                omitted = [
                    key
                    for key, text in source_segments(product.description).items()
                    if text not in represented
                    and not INSTRUCTION.search(text)
                    and context_kind(text) != "unknown"
                ]
                potential = sum(
                    len(text) + 1 for text in source_segments(product.description).values()
                )
                potential += sum(
                    len(block.text) + 1 for block in palette.fields["seo_description"].values()
                )
                if not attempt and omitted and potential >= limits.description_min:
                    raise InvalidOutput(
                        f"{exc} Повторно просмотри содержательные фрагменты {omitted}. "
                        "Извлеки назначение, считывание, монтаж, проверки и ограничения целиком."
                    ) from exc
                raise
            break
        except InvalidOutput as exc:
            if attempt:
                raise LLMError(
                    "Не удалось извлечь подтверждённые факты. "
                    "Уточните описание или попробуйте другую модель."
                ) from exc
            extraction_payload["validation_feedback"] = str(exc)

    copy_payload = {
        "fragments": palette.for_prompt(limits),
        "seo_limits": limits.model_dump(),
    }
    system = COPY_SYSTEM.format(**limits.model_dump())
    for attempt in range(2):
        try:
            plan = await llm.complete(system, copy_payload, palette.schema(limits))
            copy, grounding = palette.render(plan, limits)
            break
        except InvalidOutput as exc:
            if attempt:
                raise LLMError(
                    "Модель не прошла проверку структуры, размеров текста или фактов. "
                    "Карточка не выдана. Добавьте исходные данные, измените границы длины "
                    "или попробуйте другую модель."
                ) from exc
            copy_payload["validation_feedback"] = str(exc)

    warnings = [
        "Черновик: проверьте формулировки и назначение по исходному паспорту. "
        "Автоматическая проверка не гарантирует смысловую достоверность текста."
    ]
    if any(f.source == "description_quote" for f in facts):
        warnings.append(
            "Сведения из описания сохранены целыми исходными фрагментами, без свободного пересказа."
        )
    if conflicts:
        warnings.append("Противоречащие характеристики исключены из фактов для генерации.")
    if missing:
        warnings.append("Есть недостающие данные. Уточните их перед публикацией.")
    card = ProductCard(
        **copy.model_dump(),
        grounding=grounding,
        characteristics=characteristics,
        missing_fields=missing,
        conflicts=conflicts,
        warnings=warnings,
        facts=facts,
        status="needs_review" if conflicts or missing else "draft",
    )
    return GenerationResult(
        card=card,
        markdown=markdown_card(card),
        model=llm.model,
        elapsed_seconds=round(time.monotonic() - started, 2),
        llm_calls=llm.calls,
        applied_limits=limits,
    )
