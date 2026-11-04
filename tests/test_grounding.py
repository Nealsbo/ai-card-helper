"""Regression cases from the project review: associations and complete context."""

import asyncio
import json

import pytest

from app.composer import CopyPlan, build_palette
from app.config import Settings
from app.llm import InvalidOutput, LLMClient, LLMError
from app.models import Extraction, ProductInput, SeoLimits
from app.service import build_facts, generate
from helpers import transport_with


def extract(value, label="Интерфейс", spec_key="Интерфейс", source_id="S1"):
    return Extraction(
        facts=[dict(value=value, label=label, spec_key=spec_key, source_id=source_id)]
    )


@pytest.mark.parametrize(
    "text",
    [
        "Прибор не поддерживает RS-485.",
        "The device does not support RS-485.",
        "RS-485 is not supported.",
        "Интерфейс: отсутствует.",
        "RS-485 поддерживается только при наличии преобразователя.",
    ],
)
def test_negative_or_conditional_support_is_never_an_affirmative_parameter(text):
    product = ProductInput(name="Прибор", specs={"Интерфейс": "RS-485"}, description=text)
    facts, conflicts, _, _ = build_facts(
        product, extract("RS-485" if "RS-485" in text else "отсутствует")
    )
    assert conflicts
    assert not facts
    # No value from the quarantined source is offered to the composition model.
    palette = build_palette(product, facts, [], conflicts)
    assert not any(
        "RS-485" in f.text for blocks in palette.fields.values() for f in blocks.values()
    )


def test_conflict_scan_does_not_depend_on_model_extraction():
    product = ProductInput(
        name="Прибор", specs={"Интерфейс": "RS-485"}, description="Прибор не поддерживает RS-485."
    )
    facts, conflicts, _, _ = build_facts(product, Extraction(facts=[]))
    assert conflicts and not facts


@pytest.mark.parametrize(
    "text",
    [
        "Сведения об интерфейсе не указаны.",
        "Интерфейс: данных нет.",
        "Interface is not specified.",
    ],
)
def test_unknown_information_does_not_disprove_existing_spec(text):
    product = ProductInput(name="Прибор", specs={"Интерфейс": "RS-485"}, description=text)
    facts, conflicts, _, _ = build_facts(product, Extraction(facts=[]))
    assert not conflicts
    assert len(facts) == 1 and facts[0].value == "RS-485"


def test_wrong_parameter_mapping_cannot_relabel_a_diameter_as_a_pressure():
    product = ProductInput(
        name="Прибор", specs={"Диапазон": "0–1,6 МПа"}, description="Диаметр корпуса: 100 мм."
    )
    facts, conflicts, table, _ = build_facts(product, extract("100 мм", "Диапазон", "Диапазон"))
    assert not conflicts
    assert table[0].value == "0–1,6 МПа"
    assert not any(f.label == "Диапазон" and f.value == "100 мм" for f in facts)


@pytest.mark.parametrize(
    "fabricated",
    [
        "Диапазон: 0–100 МПа. Класс точности: 100.",
        "Диапазон: 1,6–0 МПа.",
        "Диаметр: 100 МПа.",
        "Подходит для любых сред и условий.",
    ],
)
def test_reusing_known_numbers_or_inventing_qualitative_claims_is_rejected(fabricated):
    product = ProductInput(
        name="Манометр",
        specs={"Диапазон": "0–1,6 МПа", "Диаметр": "100 мм", "Класс точности": "1,5"},
    )
    facts, conflicts, _, missing = build_facts(product, Extraction(facts=[]))
    palette = build_palette(product, facts, missing, conflicts)
    plan = CopyPlan(
        seo_title=["NAME"],
        seo_description=["NAME", fabricated],
        search_queries=["Q_BUY", "Q_SPECS", "Q_DESCRIPTION"],
        commercial_block=["REVIEW"],
    )
    with pytest.raises(InvalidOutput, match="неизвестный ID"):
        palette.render(plan, SeoLimits(description_min=1))


def test_condition_is_preserved_and_cannot_be_omitted_even_if_extractor_skips_it():
    product = ProductInput(
        name="Прибор", description="Допускается для воды только при наличии фильтра."
    )
    facts, conflicts, table, missing = build_facts(product, Extraction(facts=[]))
    assert facts[0].kind == "condition"
    assert not any(item.source.startswith("description") for item in table)
    palette = build_palette(product, facts, missing, conflicts)
    plan = CopyPlan(
        seo_title=["NAME"],
        seo_description=["NAME"],
        search_queries=["Q_BUY", "Q_SPECS", "Q_DESCRIPTION"],
        commercial_block=["F1"],
    )
    with pytest.raises(InvalidOutput, match="ограничения и условия"):
        palette.render(plan, SeoLimits(description_min=1))
    plan.seo_description.append("F1")
    copy, refs = palette.render(plan, SeoLimits(description_min=1))
    assert product.description in copy.seo_description
    assert product.description == copy.commercial_block
    assert refs["commercial_block"] == ["F1"]


def test_partial_value_does_not_truncate_a_range_or_drop_its_unit():
    product = ProductInput(name="Прибор", description="Диапазон: 0–1,6 МПа.")
    facts, _, table, _ = build_facts(product, extract("1,6", "Диапазон", None))
    assert facts[0].value == "0–1,6 МПа"
    assert table[0].label == "Диапазон"


def test_unsupported_label_from_model_does_not_become_a_characteristic():
    product = ProductInput(name="Прибор", description="Для местного контроля давления воды.")
    facts, _, table, _ = build_facts(
        product, extract("воды", "Совместимость со всеми жидкостями", None)
    )
    assert facts[0].value == product.description
    assert not any("Совместимость" in item.label for item in table)


def test_insufficient_data_returns_fields_and_never_pads_text():
    seen = []
    llm = LLMClient(Settings(), transport_with([], seen))
    with pytest.raises(LLMError, match="Недостающие поля") as exc:
        asyncio.run(generate(ProductInput(name="Манометр"), llm))
    assert exc.value.status_code == 422
    assert not seen


def test_repeated_fragment_cannot_be_used_to_meet_minimum():
    product = ProductInput(name="Прибор", specs={"Диапазон": "0–100 °C"})
    facts, conflicts, _, missing = build_facts(product, Extraction(facts=[]))
    palette = build_palette(product, facts, missing, conflicts)
    plan = CopyPlan(
        seo_title=["NAME"],
        seo_description=["NAME", "F1", "F1"],
        search_queries=["Q_BUY", "Q_SPECS", "Q_DESCRIPTION"],
        commercial_block=["REVIEW"],
    )
    with pytest.raises(InvalidOutput, match="повторять"):
        palette.render(plan, SeoLimits(description_min=1))


def test_empty_spec_is_filled_only_from_an_explicit_source_pair():
    product = ProductInput(
        name="Прибор", specs={"Диапазон": None}, description="Диапазон: 0–100 °C."
    )
    facts, conflicts, table, missing = build_facts(product, extract("100", "Диапазон", "Диапазон"))
    assert not conflicts
    assert facts[0].value == "0–100 °C"
    assert table[0].source == "description"
    assert "Диапазон" not in missing


def test_coverage_retry_uses_omitted_sources_without_invented_padding():
    product = ProductInput(
        name="Прибор",
        manufacturer="Завод",
        specs={"Диапазон": "0–100 °C", "Точность": "1"},
        description="Для контроля давления воды. Показания считывают по месту установки.",
    )
    seen = []
    extraction = Extraction(
        facts=[
            dict(
                label="Назначение",
                value="Для контроля давления воды.",
                spec_key=None,
                source_id="S1",
            ),
            dict(
                label="Считывание",
                value="Показания считывают по месту установки.",
                spec_key=None,
                source_id="S2",
            ),
        ]
    )
    plan = dict(
        seo_title=["NAME"],
        seo_description=["NAME", "F1", "F2", "F3", "F4"],
        search_queries=["Q_BUY", "Q_SPECS", "Q_DESCRIPTION"],
        commercial_block=["F3"],
    )
    # Identity+specs do not reach the minimum. Both source statements make it feasible.
    limits = SeoLimits(description_min=110, description_max=200)
    llm = LLMClient(
        Settings(seo_limits=limits),
        transport_with([{"facts": []}, extraction.model_dump(), plan], seen),
    )
    result = asyncio.run(generate(product, llm, limits))
    assert result.llm_calls == 3
    assert "S2" in json.loads(seen[1]["messages"][1]["content"])["validation_feedback"]
    assert "Показания считывают по месту установки." in result.card.seo_description
