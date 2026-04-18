"""LLM client — Gemini (preferido) con auto-fallback a OpenAI.

Usa la librería openai con el endpoint compatible de Gemini.
Centraliza la configuración para title_enricher, ai_explanation y market_intelligence.

Si Gemini falla (rate limit 429, error 500, timeout), automáticamente
usa OpenAI para el resto del request.
"""

import logging

import openai

from app.config import settings

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_MODEL_FAST = "gemini-2.5-flash-lite"
_OPENAI_MODEL = "gpt-4o-mini"

# Flag por proceso: si Gemini falla, desactivar para evitar más errores
_gemini_disabled = False


def get_llm_client(fast: bool = False) -> tuple[openai.AsyncOpenAI, str] | tuple[None, None]:
    """Retorna (client, model_name) para el proveedor LLM disponible.

    Args:
        fast: Si True, usa gemini-2.0-flash (sin thinking) para tareas simples
              como title enrichment. Si False, usa gemini-2.5-flash (con thinking)
              para tareas complejas como AI explanation.

    Prioridad: Gemini > OpenAI. Retorna (None, None) si no hay API key.
    Si Gemini fue deshabilitado por errores, usa OpenAI directamente.
    """
    if settings.gemini_api_key and not _gemini_disabled:
        model = _GEMINI_MODEL_FAST if fast else _GEMINI_MODEL
        return openai.AsyncOpenAI(
            api_key=settings.gemini_api_key,
            base_url=_GEMINI_BASE_URL,
            max_retries=0,
        ), model

    if settings.openai_api_key:
        return openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            max_retries=0,
        ), _OPENAI_MODEL

    return None, None


def reset_gemini() -> None:
    """Re-habilita Gemini. Llamar al inicio de cada request."""
    global _gemini_disabled
    _gemini_disabled = False


def disable_gemini(reason: str) -> None:
    """Desactiva Gemini para el resto del request (fallback a OpenAI)."""
    global _gemini_disabled
    if not _gemini_disabled and settings.openai_api_key:
        _gemini_disabled = True
        logger.warning("Gemini deshabilitado (%s), usando OpenAI fallback", reason)


def is_gemini_error(exc: Exception) -> bool:
    """Detecta si un error es de rate limit o server error de Gemini."""
    if isinstance(exc, openai.RateLimitError):
        return True
    if isinstance(exc, openai.InternalServerError):
        return True
    if isinstance(exc, openai.APIStatusError) and exc.status_code in (404, 429, 500, 503):
        return True
    return False


def has_llm() -> bool:
    """True si hay algún proveedor LLM configurado."""
    return bool(settings.gemini_api_key or settings.openai_api_key)
