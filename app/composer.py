"""The model selects IDs; only the server renders source-derived text."""

from dataclasses import dataclass

from pydantic import Field, create_model

from app.grounding import normalized
from app.llm import InvalidOutput, LLMError
from app.models import (
    Conflict,
    Fact,
    ProductInput,
    SeoLimits,
    StrictModel,
    copy_schema_for,
)


class CopyPlan(StrictModel):
    seo_title: list[str]
    seo_description: list[str]
    search_queries: list[str]
    commercial_block: list[str]


@dataclass(frozen=True)
class Fragment:
    text: str
    fact_ids: tuple[str, ...] = ()
    required: bool = False


@dataclass
class Palette:
    fields: dict[str, dict[str, Fragment]]

    def for_prompt(self, limits: SeoLimits):
        return {
            field: [
                {
                    "id": key,
                    "text": value.text,
                    "chars": len(value.text),
                    "required": value.required,
                }
                for key, value in blocks.items()
                if field != "seo_title" or limits.title_min <= len(value.text) <= limits.title_max
            ]
            for field, blocks in self.fields.items()
        }

    def schema(self, limits: SeoLimits):
        definitions = {}
        for field, blocks in self.fields.items():
            minimum, maximum = (3, 5) if field == "search_queries" else (1, len(blocks))
            keys = list(blocks)
            if field == "seo_title":
                maximum = 1
                keys = [
                    key
                    for key in keys
                    if limits.title_min <= len(blocks[key].text) <= limits.title_max
                ]
            definitions[field] = (
                list[str],
                Field(
                    min_length=minimum,
                    max_length=maximum,
                    json_schema_extra={"items": {"type": "string", "enum": keys}},
                ),
            )
        return create_model("GroundedCopyPlan", __base__=CopyPlan, **definitions)

    def render(self, plan: CopyPlan, limits: SeoLimits):
        values, grounding = {}, {}
        for field, blocks in self.fields.items():
            ids = getattr(plan, field)
            if len(ids) != len(set(ids)):
                raise InvalidOutput(f"{field}: нельзя повторять фрагменты для заполнения объёма")
            if not ids or any(key not in blocks for key in ids):
                raise InvalidOutput(
                    f"{field}: неизвестный ID фрагмента; используй только предложенные ID"
                )
            mandatory = {key for key, block in blocks.items() if block.required}
            if not mandatory.issubset(ids):
                raise InvalidOutput(
                    f"{field}: обязательно сохрани ограничения и условия: {sorted(mandatory)}"
                )
            if field == "seo_title" and len(ids) != 1:
                raise InvalidOutput("seo_title: выбери один готовый заголовок")
            if field == "seo_description" and "NAME" not in ids:
                raise InvalidOutput(f"{field}: необходимо включить NAME")
            selected = [blocks[key] for key in ids]
            if field == "search_queries":
                values[field] = [block.text for block in selected]
            else:
                values[field] = " ".join(block.text for block in selected)
            grounding[field] = list(
                dict.fromkeys(ref for block in selected for ref in block.fact_ids)
            )
        # Length is checked on rendered text, not on short reference IDs.
        try:
            copy = copy_schema_for(limits)(**values)
        except ValueError as exc:
            lengths = (
                f"Заголовок: {len(values['seo_title'])}, описание: {len(values['seo_description'])}, "
                f"блок менеджера: {len(values['commercial_block'])} (максимум 900)."
            )
            raise InvalidOutput(
                f"{lengths} Соблюдай seo_limits после соединения выбранных фрагментов. {exc}"
            ) from exc
        return copy, grounding

    def check_feasible(self, limits: SeoLimits, missing: list[str]):
        if len(self.fields["seo_title"]["NAME"].text) > limits.title_max:
            raise LLMError(
                "Название товара длиннее максимума SEO-заголовка. Увеличьте границу длины.", 422
            )
        if not any(
            limits.title_min <= len(block.text) <= limits.title_max
            for block in self.fields["seo_title"].values()
        ):
            raise LLMError(
                "Из исходных данных нельзя составить заголовок выбранной длины. Измените границы.",
                422,
            )
        maximum = sum(len(block.text) for block in self.fields["seo_description"].values())
        maximum += len(self.fields["seo_description"]) - 1
        if maximum < limits.description_min:
            details = " Недостающие поля: " + ", ".join(missing) + "." if missing else ""
            raise LLMError(
                f"Подтверждённых данных недостаточно для описания от {limits.description_min} символов. "
                f"Доступные фрагменты дают не более {maximum} символов без повторов. "
                "Добавьте описание или уменьшите минимальную длину." + details,
                422,
            )


def sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def build_palette(
    product: ProductInput, facts: list[Fact], missing: list[str], conflicts: list[Conflict]
):
    title = {"NAME": Fragment(product.name, ("name",))}
    identity = product.name
    if product.manufacturer:
        title["T_MAKER"] = Fragment(
            f"{product.name} — {product.manufacturer}", ("name", "manufacturer")
        )
        identity += f". Производитель: {product.manufacturer}"
    description = {
        "NAME": Fragment(
            sentence(identity), ("name",) + (("manufacturer",) if product.manufacturer else ())
        )
    }
    commercial = {}
    queries = {
        "Q_BUY": Fragment(f"Купить {product.name}", ("name",)),
        "Q_SPECS": Fragment(f"{product.name} характеристики", ("name",)),
        "Q_DESCRIPTION": Fragment(f"{product.name} описание", ("name",)),
    }
    seen = {normalized(block.text) for block in description.values()}
    for fact in facts:
        if fact.kind == "parameter":
            title[f"T_{fact.id}"] = Fragment(
                f"{product.name} — {fact.label}: {fact.value}", ("name", fact.id)
            )
            if product.manufacturer:
                title[f"TM_{fact.id}"] = Fragment(
                    f"{product.name} — {product.manufacturer} — {fact.label}: {fact.value}",
                    ("name", "manufacturer", fact.id),
                )
            text = (
                sentence(f"{fact.label}: {fact.value}")
                if fact.source == "specs"
                else sentence(fact.evidence)
            )
            query = f"{product.name}: {fact.label} {fact.value}"
            if len(query) <= 650:
                queries[f"Q_{fact.id}"] = Fragment(query, ("name", fact.id))
        else:
            text = sentence(fact.evidence)
        block = Fragment(text, (fact.id,), fact.kind in {"restriction", "condition"})
        if normalized(text) not in seen:
            description[fact.id] = block
            seen.add(normalized(text))
        # Source statements preserve purpose, conditions and restrictions verbatim.
        if fact.kind != "parameter":
            commercial[fact.id] = block
    if not commercial:
        commercial["REVIEW"] = Fragment(
            "Назначение и применимость необходимо уточнить по паспорту."
        )
    if missing:
        description["MISSING"] = Fragment("Требуют уточнения: " + ", ".join(missing) + ".")
    if conflicts:
        description["CONFLICTS"] = Fragment(
            "Есть противоречия по параметрам: "
            + ", ".join(c.field for c in conflicts)
            + ". Требуется уточнение по паспорту."
        )
    return Palette(
        {
            "seo_title": title,
            "seo_description": description,
            "search_queries": queries,
            "commercial_block": commercial,
        }
    )
