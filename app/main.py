import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.config import Settings
from app.llm import LLMError, LLMClient
from app.models import (
    GenerationRequest,
    GenerationResult,
    ProductInput,
    SeoLimits,
    SEO_TITLE_CAP,
    SEO_DESCRIPTION_CAP,
)
from app.service import generate

ROOT = Path(__file__).resolve().parent.parent


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Паспорт → Карточка", version="0.1.0", redoc_url=None)
    generation_lock = asyncio.Lock()

    @app.exception_handler(LLMError)
    async def llm_error(_request: Request, exc: LLMError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"message": str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def input_error(_request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            field = ".".join(str(p) for p in error["loc"] if p != "body") or "JSON"
            messages = {
                "json_invalid": "Некорректный JSON",
                "missing": "Обязательное поле отсутствует",
                "string_too_short": "Поле не должно быть пустым",
                "string_type": "Ожидается строка",
                "dict_type": "Ожидается объект с характеристиками",
                "extra_forbidden": "Неизвестное поле",
            }
            errors.append({"field": field, "message": messages.get(error["type"], error["msg"])})
        return JSONResponse(
            status_code=422,
            content={"error": {"message": "Проверьте входные данные", "fields": errors}},
        )

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        if request.method == "POST":
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 100_000:
                    return JSONResponse(
                        status_code=413,
                        content={"error": {"message": "Слишком большой запрос. Максимум 100 КБ."}},
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path == "/" or request.url.path.startswith(("/api/", "/static/")):
            response.headers["Cache-Control"] = "no-store"
        policy = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        # FastAPI's Swagger page uses a CDN and an inline initializer; the app itself does not.
        if request.url.path in {"/docs", "/docs/oauth2-redirect"}:
            policy = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
        response.headers["Content-Security-Policy"] = policy
        return response

    @app.get("/", include_in_schema=False)
    async def index():
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        for filename in ("app.js", "style.css"):
            digest = hashlib.sha256((ROOT / "app/static" / filename).read_bytes()).hexdigest()[:12]
            html = re.sub(
                rf'"/static/{re.escape(filename)}(?:\?[^"\s]*)?"',
                f'"/static/{filename}?v={digest}"',
                html,
            )
        return HTMLResponse(html)

    @app.get("/api/examples")
    async def examples():
        return json.loads((ROOT / "examples/products.json").read_text(encoding="utf-8"))

    @app.get("/api/settings")
    async def public_settings():
        return {
            "seo_limits": settings.seo_limits.model_dump(),
            "bounds": {"title_max": SEO_TITLE_CAP, "description_max": SEO_DESCRIPTION_CAP},
        }

    @app.get("/api/health")
    async def health(model: str | None = Query(default=None, min_length=1, max_length=300)):
        try:
            return await LLMClient(settings, transport, model=model).check_connection()
        except LLMError as exc:
            return JSONResponse(
                status_code=503,
                content={"connected": False, "model": model or settings.model, "error": str(exc)},
            )

    @app.post("/api/generate", response_model=GenerationResult)
    async def generate_card(
        product: GenerationRequest,
        model: str | None = Query(default=None, min_length=1, max_length=300),
    ):
        overrides = product.seo_limits.model_dump(exclude_none=True) if product.seo_limits else {}
        try:
            limits = SeoLimits(**{**settings.seo_limits.model_dump(), **overrides})
        except ValidationError as exc:
            errors = exc.errors(include_input=False)
            for error in errors:
                error["loc"] = ("body", "seo_limits", *error["loc"])
            raise RequestValidationError(errors) from exc
        if generation_lock.locked():
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "message": "Модель уже готовит карточку. Дождитесь завершения запроса."
                    }
                },
            )
        async with generation_lock:
            data = ProductInput.model_validate(product.model_dump(exclude={"seo_limits"}))
            return await generate(data, LLMClient(settings, transport, model=model), limits=limits)

    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    return app


app = create_app()
