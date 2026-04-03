"""LLM client for engram review — thin wrapper around Anthropic SDK."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_MAX_TOKENS = 4096

_SYSTEM_PROMPT = (
    "You are the Engram Reviewer. Analyze the session transcript and respond "
    "with valid JSON only. Do not include any text outside the JSON object."
)

# Guarded import — anthropic is an optional dependency.
try:
    import anthropic

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
    if TYPE_CHECKING:
        import anthropic


class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


def _use_bedrock() -> bool:
    """Check if Bedrock should be used based on environment."""
    return os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1"


def _build_client() -> anthropic.Anthropic | anthropic.AnthropicBedrock:
    """Build the appropriate Anthropic client for the environment.

    Bedrock: uses AWS_BEARER_TOKEN_BEDROCK and AWS_REGION from env.
    Direct: uses ANTHROPIC_API_KEY from env.
    """
    if _use_bedrock():
        return anthropic.AnthropicBedrock()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError(
            "No API credentials found. Set ANTHROPIC_API_KEY, or "
            "set CLAUDE_CODE_USE_BEDROCK=1 with AWS credentials."
        )
    return anthropic.Anthropic(api_key=api_key)


def _resolve_model(model: str | None) -> str:
    """Resolve model name, using Bedrock model ID when appropriate."""
    if model:
        return model
    return BEDROCK_MODEL if _use_bedrock() else DEFAULT_MODEL


def call_reviewer_llm(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = 2,
) -> str:
    """Send the review prompt to the Anthropic API and return raw text.

    Supports both direct Anthropic API and AWS Bedrock. Set
    CLAUDE_CODE_USE_BEDROCK=1 to use Bedrock with AWS credentials.

    Raises:
        LLMError: If the call fails after retries or credentials are missing.
        ImportError: If the anthropic package is not installed.
    """
    if not _HAS_ANTHROPIC:
        raise ImportError(
            "LLM support requires the anthropic package. "
            "Install with: pip install engram[llm]"
        )

    client = _build_client()
    resolved_model = _resolve_model(model)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=resolved_model,
                max_tokens=max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text  # type: ignore[union-attr]
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Authentication failed: {e}") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Bad request: {e}") from e
        except (
            anthropic.RateLimitError,
            anthropic.APIStatusError,
            anthropic.APITimeoutError,
        ) as e:
            last_error = e
            if attempt < max_retries:
                delay = attempt * 2
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s. "
                    "Retrying in %ds...",
                    attempt, max_retries, e, delay,
                )
                time.sleep(delay)

    raise LLMError(
        f"LLM call failed after {max_retries} attempts: {last_error}"
    ) from last_error
