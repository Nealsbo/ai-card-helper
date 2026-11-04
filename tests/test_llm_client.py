import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMClient, LLMError
from app.main import create_app
from app.models import Extraction


@pytest.mark.parametrize(
    "base_url,api_key",
    [
        ("http://127.0.0.1:1234/v1", ""),
        ("https://provider.example/api/v1", "test-secret"),
    ],
)
def test_same_client_checks_and_generates_on_local_and_cloud(base_url, api_key):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "configured-model"}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"facts":[]}'}, "finish_reason": "stop"}]},
        )

    settings = Settings(
        base_url=base_url + "/",
        model="configured-model",
        api_key=api_key,
        reasoning_effort="",
        temperature=None,
        response_format="json_object",
    )
    client = LLMClient(settings, httpx.MockTransport(handle))

    async def scenario():
        check = await client.check_connection()
        assert check["connected"] and check["model_available"]
        assert client.calls == 0  # No completion is purchased by a connection check.
        assert "test-secret" not in json.dumps(check)
        assert await client.complete("Extract facts", {}, Extraction) == Extraction(facts=[])

    asyncio.run(scenario())
    assert str(requests[0].url) == base_url + "/models"
    assert str(requests[1].url) == base_url + "/chat/completions"
    for request in requests:
        assert request.headers.get("Authorization") == (f"Bearer {api_key}" if api_key else None)
    body = json.loads(requests[1].content)
    assert "reasoning_effort" not in body
    assert "temperature" not in body
    assert body["response_format"] == {"type": "json_object"}


def test_optional_provider_parameters_do_not_collide():
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"facts":[]}'}, "finish_reason": "stop"}]},
        )

    settings = Settings(
        token_limit_param="max_completion_tokens",
        max_tokens=500,
        reasoning_effort="low",
        temperature=0.2,
    )
    asyncio.run(LLMClient(settings, httpx.MockTransport(handle)).complete("", {}, Extraction))
    assert captured[0]["max_completion_tokens"] == 500
    assert "max_tokens" not in captured[0]
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["temperature"] == 0.2


def test_cloud_auth_error_does_not_leak_key_or_provider_response():
    settings = Settings(
        base_url="https://provider.example/v1", model="cloud-model", api_key="secret-key"
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(401, text="PRIVATE PROVIDER DETAILS"))
    with TestClient(create_app(settings, transport)) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert not response.json()["connected"]
        assert "LLM_API_KEY" in response.json()["error"]
        assert "secret-key" not in response.text
        assert "PRIVATE" not in response.text


def test_connection_reports_missing_model_without_generating():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []}))
    result = asyncio.run(LLMClient(Settings(), transport).check_connection())
    assert result["connected"]
    assert not result["model_available"]


def test_remote_network_error_has_no_local_server_advice():
    def handle(request):
        raise httpx.ConnectError("private network details", request=request)

    with pytest.raises(LLMError) as exc:
        asyncio.run(
            LLMClient(
                Settings(base_url="https://provider.example/v1"), httpx.MockTransport(handle)
            ).check_connection()
        )
    assert "LLM_BASE_URL" in str(exc.value)
    assert "LM Studio" not in str(exc.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "provider.example",
        "file:///tmp/model",
        "https://x/v1?key=secret",
        "https://user:secret@provider.example/v1",
    ],
)
def test_invalid_endpoint_is_rejected_without_echoing_secrets(base_url):
    with pytest.raises(ValueError) as exc:
        Settings(base_url=base_url)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token_limit_param": "other"},
        {"temperature": float("nan")},
        {"temperature": 3},
        {"timeout": float("nan")},
    ],
)
def test_invalid_generation_settings(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_model_is_optional_in_configuration(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LLM_MODEL")
    else:
        monkeypatch.setenv("LLM_MODEL", value)
    assert Settings().model == ""


def test_single_model_is_resolved_once_for_multiple_completions():
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "only-model"}]})
        assert json.loads(request.content)["model"] == "only-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts":[]}'}}]})

    llm = LLMClient(Settings(model=""), httpx.MockTransport(handle))

    async def scenario():
        await llm.complete("", {}, Extraction)
        await llm.complete("", {}, Extraction)

    asyncio.run(scenario())
    assert [r.method for r in requests] == ["GET", "POST", "POST"]
    assert llm.model == "only-model"
    assert llm.settings.model == ""


def test_multiple_models_require_selection_without_starting_generation():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "one"}, {"id": "two"}]})

    llm = LLMClient(Settings(model=""), httpx.MockTransport(handle))
    check = asyncio.run(llm.check_connection())
    assert check["connected"] and check["selection_required"]
    assert check["model"] == ""
    with pytest.raises(LLMError) as exc:
        asyncio.run(llm.complete("", {}, Extraction))
    assert exc.value.status_code == 409
    assert all(r.method == "GET" for r in requests)
    assert llm.calls == 0


def test_empty_list_cannot_be_selected_automatically():
    llm = LLMClient(
        Settings(model=""), httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []}))
    )
    with pytest.raises(LLMError) as exc:
        asyncio.run(llm.resolve_model())
    assert exc.value.status_code == 503


def test_connection_auto_selects_single_model():
    llm = LLMClient(
        Settings(model=""),
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "only"}]})),
    )
    result = asyncio.run(llm.check_connection())
    assert result["model"] == "only"
    assert result["model_available"]
    assert not result["selection_required"]


def test_configured_model_has_priority_over_auto_selection():
    llm = LLMClient(
        Settings(model="configured"),
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "different"}]})),
    )
    result = asyncio.run(llm.check_connection())
    assert result["model"] == "configured"
    assert not result["model_available"]
    assert asyncio.run(llm.resolve_model()) == "configured"


def test_explicit_request_model_overrides_config_without_mutation():
    settings = Settings(model="configured")
    llm = LLMClient(settings, model=" selected ")
    assert asyncio.run(llm.resolve_model()) == "selected"
    assert settings.model == "configured"
