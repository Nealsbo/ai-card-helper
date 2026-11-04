import json
import logging
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class InvalidOutput(Exception):
    pass


class LLMClient:
    """One Chat Completions client for local and hosted compatible APIs."""

    def __init__(self, settings: Settings, transport=None, model: str | None = None):
        self.settings = settings
        self.transport = transport
        self.calls = 0
        self.model = settings.model if model is None else model.strip()
        if model is not None and not self.model:
            raise LLMError("Выберите непустой идентификатор модели.", 422)

    def client(self):
        headers = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return httpx.AsyncClient(
            base_url=self.settings.base_url + "/",
            headers=headers,
            timeout=httpx.Timeout(self.settings.timeout, connect=5),
            transport=self.transport,
            trust_env=False,
        )

    async def request(self, method: str, path: str, **kwargs):
        try:
            async with self.client() as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise LLMError(
                "API модели не ответил вовремя. Проверьте доступность сервиса и тайм-аут.", 504
            ) from exc
        except httpx.ConnectError as exc:
            raise LLMError(
                "Нет подключения к API модели. Проверьте LLM_BASE_URL и доступ к сервису.", 503
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = {
                401: "LLM отклонила авторизацию. Проверьте LLM_API_KEY.",
                403: "LLM запретила доступ. Проверьте настройки сервера и ключ.",
                404: "LLM не найдена. Проверьте LLM_MODEL и адрес API.",
                429: "LLM перегружена или достигнут лимит запросов. Повторите позже.",
                400: "API отклонил запрос. Проверьте модель, формат ответа и параметры генерации.",
            }.get(status, f"Ошибка сервера LLM (HTTP {status}). Повторите позже.")
            raise LLMError(message, 503 if status == 429 else 502) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError("Сервер LLM вернул некорректный ответ или разорвал соединение.") from exc

    async def models(self):
        data = await self.request("GET", "models", timeout=5.0)
        try:
            return list(
                dict.fromkeys(
                    item["id"]
                    for item in data["data"]
                    if isinstance(item.get("id"), str) and item["id"].strip()
                )
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise LLMError("Сервер LLM вернул некорректный список моделей.") from exc

    async def check_connection(self):
        models = await self.models()
        if not self.model and len(models) == 1:
            self.model = models[0]
        return {
            "connected": True,
            "model": self.model,
            "configured_model": self.settings.model,
            "model_available": bool(self.model) and self.model in models,
            "selection_required": not self.model and len(models) > 1,
            "available_models": models,
        }

    async def resolve_model(self):
        if self.model:
            return self.model
        models = await self.models()
        if not models:
            raise LLMError("API вернул пустой список моделей. Проверьте настройки провайдера.", 503)
        if len(models) > 1:
            raise LLMError(
                "Доступно несколько моделей. Нажмите «Проверить соединение» и выберите модель "
                "в форме или задайте LLM_MODEL в .env.",
                409,
            )
        self.model = models[0]
        return self.model

    async def complete(self, system: str, payload: dict, schema: type[T]) -> T:
        body = {
            "model": await self.resolve_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            self.settings.token_limit_param: self.settings.max_tokens,
            "stream": False,
        }
        if self.settings.temperature is not None:
            body["temperature"] = self.settings.temperature
        if self.settings.reasoning_effort:
            body["reasoning_effort"] = self.settings.reasoning_effort
        if self.settings.response_format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }
        elif self.settings.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        # The schema is also in the prompt for compatible servers without constrained decoding.
        body["messages"][0]["content"] += "\nJSON Schema:\n" + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )
        self.calls += 1
        logger.info("LLM request %s: %s", self.calls, schema.__name__)
        data = await self.request("POST", "chat/completions", json=body)
        logger.info("LLM response %s received", self.calls)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if not message.get("content") and message.get("reasoning_content"):
                raise LLMError(
                    "Модель вернула только рассуждения без JSON. Проверьте "
                    "LLM_REASONING_EFFORT и лимит LLM_MAX_TOKENS для выбранной модели."
                )
            if choice.get("finish_reason") == "length":
                raise InvalidOutput("Ответ обрезан лимитом токенов")
            content = message["content"]
            if not isinstance(content, str):
                raise InvalidOutput("В ответе модели нет текста")
            # Some local models put a completed reasoning block before the JSON.
            content = re.sub(r"^\s*<think>.*?</think>\s*", "", content, flags=re.S)
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            return schema.model_validate_json(content)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in error['loc']) or 'JSON'}: {error['msg']}"
                for error in exc.errors(include_input=False)[:5]
            )
            raise InvalidOutput(f"Ответ модели не соответствует JSON-схеме: {details}") from exc
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise InvalidOutput("Ответ модели не соответствует JSON-схеме") from exc
