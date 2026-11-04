import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from helpers import transport_with

from app.config import Settings
from app.llm import LLMClient, LLMError
from app.main import create_app
from app.models import ProductInput, SeoLimits, copy_schema_for
from app.service import generate


def test_configuration_reads_all_four_limits(monkeypatch):
    for name, value in {
        "SEO_TITLE_MIN_CHARS": "20",
        "SEO_TITLE_MAX_CHARS": "90",
        "SEO_DESCRIPTION_MIN_CHARS": "100",
        "SEO_DESCRIPTION_MAX_CHARS": "250",
    }.items():
        monkeypatch.setenv(name, value)
    assert Settings().seo_limits == SeoLimits(
        title_min=20, title_max=90, description_min=100, description_max=250
    )


def test_absent_configuration_uses_original_task_limits(monkeypatch):
    for name in [
        "SEO_TITLE_MIN_CHARS",
        "SEO_TITLE_MAX_CHARS",
        "SEO_DESCRIPTION_MIN_CHARS",
        "SEO_DESCRIPTION_MAX_CHARS",
    ]:
        monkeypatch.delenv(name)
    assert Settings().seo_limits == SeoLimits()


@pytest.mark.parametrize(
    "overrides",
    [
        {"title_min": 0},
        {"title_max": 301},
        {"description_max": 10001},
        {"title_min": 71, "title_max": 70},
        {"description_min": 1201},
        {"title_min": True},
        {"description_max": 1.5},
        {"title_max": "90"},
    ],
)
def test_invalid_limits_are_rejected(overrides):
    with pytest.raises(ValidationError):
        SeoLimits(**overrides)


def test_invalid_environment_bounds_fail_at_startup(monkeypatch):
    monkeypatch.setenv("SEO_DESCRIPTION_MIN_CHARS", "1500")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["", "1.5", "abc"])
def test_non_integer_environment_value_names_the_setting(monkeypatch, value):
    monkeypatch.setenv("SEO_TITLE_MIN_CHARS", value)
    with pytest.raises(ValueError, match="SEO_TITLE_MIN_CHARS"):
        Settings()


def test_public_settings_expose_lengths_without_credentials():
    limits = SeoLimits(title_min=10, title_max=80, description_min=200, description_max=500)
    with TestClient(create_app(Settings(seo_limits=limits, api_key="test-secret"))) as client:
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["seo_limits"] == limits.model_dump()
        assert "test-secret" not in response.text
        assert "api_key" not in response.text
        assert response.json()["bounds"] == {"title_max": 300, "description_max": 10000}


def plan(**changes):
    return {
        "seo_title": ["NAME"],
        "seo_description": ["NAME", "F1"],
        "search_queries": ["Q_BUY", "Q_SPECS", "Q_DESCRIPTION"],
        "commercial_block": ["REVIEW"],
        **changes,
    }


def test_selected_lengths_reach_prompt_schema_and_metadata():
    limits = SeoLimits(title_min=5, title_max=100, description_min=20, description_max=80)
    seen = []
    product = ProductInput(name="Манометр", specs={"Диапазон": "0–1,6 МПа"})
    result = asyncio.run(
        generate(product, LLMClient(Settings(seo_limits=limits), transport_with([plan()], seen)))
    )
    body = seen[0]
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["seo_title"]["items"]["enum"] == ["NAME", "T_F1"]
    assert "5–100" in body["messages"][0]["content"]
    assert "20–80" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"])["seo_limits"] == limits.model_dump()
    assert result.applied_limits == limits
    assert result.card.seo_title == product.name


def test_request_override_is_merged_with_config_and_does_not_persist():
    configured = SeoLimits(title_max=60, description_min=20, description_max=80)
    seen = []
    transport = transport_with([plan(), plan()], seen)
    product = {"name": "Манометр", "specs": {"Диапазон": "0–1,6 МПа"}}
    with TestClient(create_app(Settings(seo_limits=configured), transport)) as client:
        response = client.post("/api/generate", json={**product, "seo_limits": {"title_max": 100}})
        assert response.status_code == 200
        assert response.json()["applied_limits"]["title_max"] == 100
        assert client.get("/api/settings").json()["seo_limits"] == configured.model_dump()
        response = client.post("/api/generate", json=product)
        assert response.status_code == 200
        assert response.json()["applied_limits"] == configured.model_dump()


@pytest.mark.parametrize(
    "override",
    [
        {"title_min": 80},
        {"description_max": 10},
        {"title_min": 0},
        {"title_max": 301},
        {"description_min": 1.5},
        {"title_max": "100"},
        {"unknown": 1},
    ],
)
def test_invalid_request_limits_return_422_without_calling_model(override):
    def fail_on_network(_):
        raise AssertionError("Invalid options must fail before contacting the model")

    settings = Settings(
        seo_limits=SeoLimits(title_min=10, title_max=60, description_min=20, description_max=80)
    )
    with TestClient(create_app(settings, httpx.MockTransport(fail_on_network))) as client:
        response = client.post("/api/generate", json={"name": "Манометр", "seo_limits": override})
        assert response.status_code == 422
        assert any("seo_limits" in item["field"] for item in response.json()["error"]["fields"])


def test_each_generation_step_receives_only_relevant_data():
    limits = SeoLimits(description_min=20, description_max=500)
    seen = []
    with TestClient(
        create_app(
            Settings(seo_limits=limits),
            transport_with([{"facts": []}, plan(seo_description=["NAME", "MISSING"])], seen),
        )
    ) as client:
        response = client.post(
            "/api/generate",
            json={
                "name": "Манометр",
                "description": "Для контроля давления воды.",
                "seo_limits": {"title_max": 100},
            },
        )
        assert response.status_code == 200
    extraction = json.loads(seen[0]["messages"][1]["content"])
    assert "seo_limits" not in extraction
    assert "title_max" not in extraction
    writer = json.loads(seen[1]["messages"][1]["content"])
    assert writer["seo_limits"]["title_max"] == 100


def test_length_violation_gets_one_correction_with_useful_feedback():
    limits = SeoLimits(title_min=1, title_max=60, description_min=20, description_max=80)
    seen = []
    llm = LLMClient(
        Settings(seo_limits=limits), transport_with([plan(seo_description=["NAME"]), plan()], seen)
    )
    result = asyncio.run(
        generate(ProductInput(name="Манометр", specs={"Диапазон": "0–1,6 МПа"}), llm)
    )
    assert result.llm_calls == 2
    payload = json.loads(seen[1]["messages"][1]["content"])
    assert "описание: 9" in payload["validation_feedback"].casefold()


def test_repeated_violation_is_not_returned_as_success():
    limits = SeoLimits(description_min=20, description_max=80)
    llm = LLMClient(
        Settings(seo_limits=limits),
        transport_with([plan(seo_description=["NAME"]), plan(seo_description=["NAME"])], []),
    )
    with pytest.raises(LLMError, match="границы длины"):
        asyncio.run(generate(ProductInput(name="Манометр", specs={"Диапазон": "0–1,6 МПа"}), llm))


def test_exact_boundaries_are_valid_and_count_spaces():
    text = "Текст с пробелами"
    title = "Название"
    limits = SeoLimits(
        title_min=len(title),
        title_max=len(title),
        description_min=len(text),
        description_max=len(text),
    )
    values = dict(
        seo_title=title,
        seo_description=text,
        search_queries=["query one", "query two", "query three"],
        commercial_block="Source text",
    )
    copy = copy_schema_for(limits)(**values)
    assert len(copy.seo_description) == len(text)
    with pytest.raises(ValidationError):
        copy_schema_for(limits)(**{**values, "seo_description": text + "!"})
