import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dotenv import load_dotenv
from app.models import SeoLimits

load_dotenv()


def seo_limits_from_env() -> SeoLimits:
    variables = {
        "title_min": "SEO_TITLE_MIN_CHARS",
        "title_max": "SEO_TITLE_MAX_CHARS",
        "description_min": "SEO_DESCRIPTION_MIN_CHARS",
        "description_max": "SEO_DESCRIPTION_MAX_CHARS",
    }
    values = {}
    for name, variable in variables.items():
        if variable in os.environ:
            try:
                values[name] = int(os.environ[variable])
            except ValueError as exc:
                raise ValueError(f"{variable}: ожидается целое число символов") from exc
    return SeoLimits(**values)


@dataclass(frozen=True)
class Settings:
    seo_limits: SeoLimits = field(default_factory=seo_limits_from_env)
    base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    )
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""), repr=False)
    timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "180")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "3500")))
    response_format: str = field(
        default_factory=lambda: os.getenv("LLM_RESPONSE_FORMAT", "json_schema")
    )
    reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", ""))
    token_limit_param: str = field(
        default_factory=lambda: os.getenv("LLM_TOKEN_LIMIT_PARAM", "max_tokens")
    )
    temperature: float | None = field(
        default_factory=lambda: (
            float(os.environ["LLM_TEMPERATURE"])
            if os.getenv("LLM_TEMPERATURE", "").strip()
            else None
        )
    )

    def __post_init__(self):
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))
        object.__setattr__(self, "model", self.model.strip())
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment:
            raise ValueError(
                "LLM_BASE_URL должен быть абсолютным HTTP(S)-адресом API без query и fragment"
            )
        if url.username is not None or url.password is not None:
            raise ValueError("Задайте ключ через LLM_API_KEY, а не в LLM_BASE_URL")
        if self.token_limit_param not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("LLM_TOKEN_LIMIT_PARAM: допустимы max_tokens, max_completion_tokens")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("LLM_TEMPERATURE должна быть от 0 до 2 или пустой")
        if self.response_format not in {"json_schema", "json_object", "none"}:
            raise ValueError("LLM_RESPONSE_FORMAT: допустимы json_schema, json_object, none")
        if not math.isfinite(self.timeout) or self.timeout <= 0 or self.max_tokens <= 0:
            raise ValueError("Тайм-аут и лимит токенов должны быть положительными")
