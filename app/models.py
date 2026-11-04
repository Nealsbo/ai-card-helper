from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    create_model,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
ValueText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
SEO_TITLE_CAP = 300
SEO_DESCRIPTION_CAP = 10000
TitleLength = Annotated[int, Field(strict=True, ge=1, le=SEO_TITLE_CAP)]
DescriptionLength = Annotated[int, Field(strict=True, ge=1, le=SEO_DESCRIPTION_CAP)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SeoLimits(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title_min: TitleLength = 1
    title_max: TitleLength = 70
    description_min: DescriptionLength = 800
    description_max: DescriptionLength = 1200

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.title_min > self.title_max:
            raise ValueError("Минимум заголовка не должен превышать максимум")
        if self.description_min > self.description_max:
            raise ValueError("Минимум описания не должен превышать максимум")
        return self


class SeoLimitsOverride(StrictModel):
    title_min: TitleLength | None = None
    title_max: TitleLength | None = None
    description_min: DescriptionLength | None = None
    description_max: DescriptionLength | None = None


class ProductInput(StrictModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    manufacturer: ShortText = ""
    specs: dict[ShortText, ValueText | None] = Field(default_factory=dict, max_length=50)
    description: str = Field(default="", max_length=18000)

    @field_validator("specs", mode="before")
    @classmethod
    def validate_specs(cls, specs):
        if not isinstance(specs, dict):
            return specs
        normalized = {}
        for label in specs:
            if not isinstance(label, str):
                continue  # Pydantic reports the key type separately.
            if not label.strip():
                raise ValueError("Название характеристики не может быть пустым")
            if label.strip().casefold() in normalized:
                raise ValueError("Повторяющиеся названия характеристик")
            normalized[label.strip().casefold()] = True
        return specs


class GenerationRequest(ProductInput):
    seo_limits: SeoLimitsOverride | None = None


class ExtractedFact(StrictModel):
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=1000)
    source_id: str = Field(
        min_length=1, max_length=20, description="Идентификатор фрагмента S1, S2, …"
    )
    spec_key: str | None = Field(
        description="Точный ключ из specs, если это тот же параметр; иначе null"
    )


class Extraction(StrictModel):
    facts: list[ExtractedFact] = Field(max_length=40)


class GeneratedCopy(StrictModel):
    seo_title: str = Field(min_length=1, max_length=SEO_TITLE_CAP)
    seo_description: str = Field(min_length=1, max_length=SEO_DESCRIPTION_CAP)
    search_queries: list[Annotated[str, StringConstraints(min_length=1, max_length=650)]] = Field(
        min_length=3, max_length=5
    )
    commercial_block: str = Field(min_length=1, max_length=900)


def copy_schema_for(limits: SeoLimits) -> type[GeneratedCopy]:
    return create_model(
        "GeneratedCopyWithLimits",
        __base__=GeneratedCopy,
        seo_title=(str, Field(min_length=limits.title_min, max_length=limits.title_max)),
        seo_description=(
            str,
            Field(min_length=limits.description_min, max_length=limits.description_max),
        ),
    )


class Fact(StrictModel):
    id: str
    label: str
    value: str
    source: str
    evidence: str
    kind: Literal["parameter", "statement", "restriction", "condition"] = "parameter"


class Characteristic(StrictModel):
    label: str
    value: str
    source: str
    evidence: str


class Conflict(StrictModel):
    field: str
    values: list[str]
    evidence: list[str]


class ProductCard(StrictModel):
    seo_title: str
    seo_description: str
    characteristics: list[Characteristic]
    search_queries: list[str]
    commercial_block: str
    missing_fields: list[str]
    conflicts: list[Conflict]
    warnings: list[str]
    facts: list[Fact]
    status: str
    grounding: dict[str, list[str]] = Field(default_factory=dict)


class GenerationResult(StrictModel):
    card: ProductCard
    markdown: str
    model: str
    elapsed_seconds: float
    llm_calls: int
    applied_limits: SeoLimits
