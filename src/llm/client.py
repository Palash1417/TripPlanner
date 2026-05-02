"""Gemini client wrapper. Single point of truth for model selection and structured output.

Uses Google's free-tier Gemini API. Get a key at:
    https://aistudio.google.com/app/apikey
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Literal, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

ModelTier = Literal["fast", "smart"]

_DEFAULT_MODELS: dict[ModelTier, str] = {
    # Free tier: most accounts have Pro disabled (limit=0). Flash is plenty
    # capable for structured-output tasks and has 15 RPM / 1M tokens-per-day.
    # Override via GEMINI_MODEL_SMART=gemini-2.5-pro if you have paid access.
    "fast": "gemini-2.5-flash",
    "smart": "gemini-2.5-flash",
}

# Paid-tier pricing per 1M tokens (input, output). Free tier is $0 up to quota.
# Shown so we can track "what this would cost at scale" via the tracer.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.0-flash": (0.075, 0.30),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
}

# Transient HTTP codes that should trigger retry with exponential backoff.
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3

T = TypeVar("T", bound=BaseModel)


class QuotaExhaustedError(RuntimeError):
    """Raised when a hard, non-transient Gemini quota is exhausted (typically the
    free-tier *daily* request cap). Distinct from a transient minute-rate 429
    because the quota won't reset within any reasonable retry budget — retrying
    only burns more API calls. Callers should catch this and degrade gracefully
    rather than re-attempt.
    """

    def __init__(
        self,
        message: str,
        *,
        model: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.retry_after_seconds = retry_after_seconds


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    parsed: Optional[BaseModel] = None


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        fast_model: Optional[str] = None,
        smart_model: Optional[str] = None,
    ) -> None:
        self._api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        self._models: dict[ModelTier, str] = {
            "fast": fast_model or os.getenv("GEMINI_MODEL_FAST", _DEFAULT_MODELS["fast"]),
            "smart": smart_model or os.getenv("GEMINI_MODEL_SMART", _DEFAULT_MODELS["smart"]),
        }
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/app/apikey, then copy .env.example to .env."
            )
        from google import genai

        self._client = genai.Client(api_key=self._api_key)
        return self._client

    def model_for(self, tier: ModelTier) -> str:
        return self._models[tier]

    def call(
        self,
        system: str,
        user: str,
        *,
        tier: ModelTier = "smart",
        max_tokens: int = 2048,
        schema: Optional[Type[T]] = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        from google.genai import types

        model = self._models[tier]

        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
        }
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = _pydantic_to_gemini_schema(schema)

        # Gemini 2.5 has built-in "thinking" tokens that count toward the
        # max_output_tokens budget. For structured-output tasks the schema is
        # the contract — we don't need extra reasoning tokens that may truncate
        # the actual JSON output. Disable for predictable token use.
        thinking_cfg = _build_thinking_disabled(types)
        if thinking_cfg is not None:
            config_kwargs["thinking_config"] = thinking_cfg

        started = time.perf_counter()
        response = _generate_with_retry(
            client,
            model=model,
            contents=user,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = response.text or ""

        parsed: Optional[BaseModel] = None
        if schema is not None:
            parsed = getattr(response, "parsed", None)
            if parsed is None or not isinstance(parsed, schema):
                parsed = self._parse_or_raise(text, schema)

        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) or 0
        out_tok = getattr(usage, "candidates_token_count", 0) or 0

        return LLMResponse(
            text=text,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
            cost_usd=_estimate_cost(model, in_tok, out_tok),
            parsed=parsed,
        )

    @staticmethod
    def _parse_or_raise(text: str, schema: Type[T]) -> T:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM did not return valid JSON: {e}\nRaw: {text[:500]}") from e

        try:
            return schema.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"LLM JSON failed schema validation: {e}\nData: {data}") from e


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _PRICING_USD_PER_MTOK.get(model)
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def _build_thinking_disabled(types_module):
    """Construct a ThinkingConfig with budget=0 if the SDK supports it.

    Older google-genai versions don't expose ThinkingConfig — return None then.
    """
    cfg_cls = getattr(types_module, "ThinkingConfig", None)
    if cfg_cls is None:
        return None
    try:
        return cfg_cls(thinking_budget=0)
    except Exception:  # noqa: BLE001 — defensive: SDK shape may vary
        return None


def _generate_with_retry(client, *, model: str, contents, config):
    """Call generate_content, retry transient errors (429/5xx) with backoff.

    For 429 we parse Gemini's suggested `retryDelay` from the error and respect it
    (capped). For 5xx we use exponential backoff with jitter.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:  # noqa: BLE001
            code = _http_code_of(e)
            if code == 429 and _is_daily_quota_exhausted(e):
                # Daily quotas reset on a 24h boundary, not within our retry
                # budget. Fail fast so the UI can degrade gracefully instead of
                # waiting 60s only to fail again and burn more quota.
                raise QuotaExhaustedError(
                    f"Gemini daily quota exhausted for model {model!r}. "
                    f"The free tier allows 20 requests/day for gemini-2.5-flash "
                    f"and resets ~24h after the first request of the day. "
                    f"Either wait for the quota to reset or upgrade GEMINI_API_KEY "
                    f"to a paid-tier key.",
                    model=model,
                    retry_after_seconds=_retry_after_seconds(e),
                ) from e
            if code in _RETRYABLE_HTTP_CODES and attempt < _MAX_RETRIES:
                if code == 429:
                    suggested = _retry_after_seconds(e)
                    delay = min(suggested if suggested is not None else 30.0, 60.0)
                else:
                    delay = (2**attempt) + random.uniform(0, 0.3)
                print(
                    f"[llm] {code} from Gemini, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{_MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(delay)
                last_exc = e
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable retry loop exit")


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Pull Gemini's suggested retry delay (e.g. '47s') from a 429 error."""
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", str(exc))
    if match:
        return float(match.group(1))
    return None


def _is_daily_quota_exhausted(exc: BaseException) -> bool:
    """True when a 429 is the daily-cap quota (free tier `*PerDay*` quotaId).

    Gemini surfaces the offending quota in `quotaId`. Per-minute rate limits
    contain `PerMinute` and *are* worth retrying; per-day caps contain `PerDay`
    and are not. The reported `retryDelay` for daily caps is misleading
    (~59s) because it's really hours-to-midnight away.
    """
    return "PerDay" in str(exc)


def _http_code_of(exc: BaseException) -> Optional[int]:
    """Extract an HTTP status code from a google-genai exception, defensively."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    msg = str(exc).strip()
    if len(msg) >= 3 and msg[:3].isdigit():
        try:
            return int(msg[:3])
        except ValueError:
            return None
    return None


# Keywords Gemini's OpenAPI subset rejects when they appear as schema metadata.
# "title" is included here, but stripped only at schema-metadata positions —
# never from inside a `properties` map, where "title" may be a user field name.
_GEMINI_UNSUPPORTED_KEYS = ("additionalProperties", "title", "$schema", "definitions")


def _pydantic_to_gemini_schema(model_cls: Type[BaseModel]) -> dict:
    """Convert a Pydantic model into a Gemini-compatible JSON schema.

    Gemini's response_schema is an OpenAPI 3.0 subset — it rejects
    `additionalProperties`, `$ref`/`$defs`, and a few cosmetic keys that
    Pydantic emits by default. We inline refs and strip unsupported keys.
    Our StrictModel still enforces extra="forbid" when validating LLM output;
    this only affects the hint we send Gemini.
    """
    raw = model_cls.model_json_schema()
    return _strip_unsupported(_inline_refs(raw))


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                target = defs.get(ref_name, {})
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(dict(merged))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _strip_unsupported(node, *, in_properties: bool = False):
    if isinstance(node, dict):
        # Inside a `properties` map the keys are user-defined field names, not
        # schema keywords — popping "title" here would delete a real field
        # named `title` and leave it dangling in `required`.
        if not in_properties:
            for key in _GEMINI_UNSUPPORTED_KEYS:
                node.pop(key, None)
        return {
            k: _strip_unsupported(v, in_properties=(k == "properties"))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_strip_unsupported(x) for x in node]
    return node
