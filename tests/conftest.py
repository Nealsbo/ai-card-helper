import pytest


@pytest.fixture(autouse=True)
def isolated_llm_environment(monkeypatch):
    """Unit tests must not depend on the user's endpoint, credentials or model."""
    for key, value in {
        "LLM_BASE_URL": "http://127.0.0.1:1234/v1",
        "LLM_MODEL": "test-model",
        "LLM_API_KEY": "",
        "LLM_RESPONSE_FORMAT": "json_schema",
        "LLM_REASONING_EFFORT": "none",
        "LLM_TEMPERATURE": "0.15",
        "LLM_TOKEN_LIMIT_PARAM": "max_tokens",
        "LLM_TIMEOUT_SECONDS": "180",
        "LLM_MAX_TOKENS": "3500",
        "SEO_TITLE_MIN_CHARS": "1",
        "SEO_TITLE_MAX_CHARS": "70",
        "SEO_DESCRIPTION_MIN_CHARS": "800",
        "SEO_DESCRIPTION_MAX_CHARS": "1200",
    }.items():
        monkeypatch.setenv(key, value)
