"""Tests for the LLM client module."""

from __future__ import annotations

import importlib
import types
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_anthropic() -> types.ModuleType:
    """Build a mock anthropic module with realistic exception hierarchy."""
    mod = types.ModuleType("anthropic")

    # Exception classes matching the real anthropic SDK hierarchy
    mod.APIStatusError = type("APIStatusError", (Exception,), {})  # type: ignore[attr-defined]
    mod.RateLimitError = type("RateLimitError", (mod.APIStatusError,), {})  # type: ignore[attr-defined]
    mod.APITimeoutError = type("APITimeoutError", (Exception,), {})  # type: ignore[attr-defined]
    mod.AuthenticationError = type("AuthenticationError", (mod.APIStatusError,), {})  # type: ignore[attr-defined]
    mod.BadRequestError = type("BadRequestError", (mod.APIStatusError,), {})  # type: ignore[attr-defined]

    mock_client = MagicMock()
    mod.Anthropic = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]
    mod.AnthropicBedrock = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]
    mod._mock_client = mock_client  # type: ignore[attr-defined]

    return mod


def _reload_with_anthropic(mock_mod: types.ModuleType):  # type: ignore[no-untyped-def]
    """Reload engram.llm with a mock anthropic module available."""
    import engram.llm as llm_mod

    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        importlib.reload(llm_mod)
    return llm_mod


def _cleanup_llm() -> None:
    """Reload llm module to restore original state."""
    import engram.llm as llm_mod

    importlib.reload(llm_mod)


class TestCallReviewerLLM:
    def test_success_direct_api(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"decisions": []}')]
        mock_mod._mock_client.messages.create.return_value = mock_response
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            result = llm.call_reviewer_llm("test prompt")
            assert result == '{"decisions": []}'
            mock_mod.Anthropic.assert_called_once_with(api_key="test-key")
            mock_mod._mock_client.messages.create.assert_called_once()
        finally:
            _cleanup_llm()

    def test_success_bedrock(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"decisions": []}')]
        mock_mod._mock_client.messages.create.return_value = mock_response
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            result = llm.call_reviewer_llm("test prompt")
            assert result == '{"decisions": []}'
            # Should use AnthropicBedrock, not Anthropic
            mock_mod.AnthropicBedrock.assert_called_once()
            mock_mod.Anthropic.assert_not_called()
            # Should use Bedrock model ID
            call_kwargs = mock_mod._mock_client.messages.create.call_args
            assert "us.anthropic" in call_kwargs.kwargs["model"]
        finally:
            _cleanup_llm()

    def test_no_credentials_raises_llm_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            with pytest.raises(llm.LLMError, match="No API credentials"):
                llm.call_reviewer_llm("test prompt")
        finally:
            _cleanup_llm()

    def test_import_error_when_no_anthropic(self) -> None:
        import engram.llm as llm_mod

        # Reload without anthropic to set _HAS_ANTHROPIC = False
        try:
            with patch.dict(
                "sys.modules", {"anthropic": None},
            ):
                import importlib

                importlib.reload(llm_mod)
            with pytest.raises(ImportError, match="pip install engram"):
                llm_mod.call_reviewer_llm("test")
        finally:
            _cleanup_llm()

    def test_retries_on_rate_limit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"decisions": []}')]
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        rate_limit_error = mock_mod.RateLimitError("Rate limited")
        mock_mod._mock_client.messages.create.side_effect = [
            rate_limit_error, mock_response,
        ]

        try:
            llm = _reload_with_anthropic(mock_mod)
            with patch.object(llm.time, "sleep"):
                result = llm.call_reviewer_llm("test", max_retries=2)
            assert result == '{"decisions": []}'
            assert mock_mod._mock_client.messages.create.call_count == 2
        finally:
            _cleanup_llm()

    def test_retries_exhausted_raises_llm_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        server_error = mock_mod.APIStatusError("Server error")
        mock_mod._mock_client.messages.create.side_effect = server_error
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            with patch.object(llm.time, "sleep"), \
                 pytest.raises(llm.LLMError, match="failed after"):
                llm.call_reviewer_llm("test", max_retries=2)
            assert mock_mod._mock_client.messages.create.call_count == 2
        finally:
            _cleanup_llm()

    def test_no_retry_on_auth_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        auth_error = mock_mod.AuthenticationError("Invalid API key")
        mock_mod._mock_client.messages.create.side_effect = auth_error
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            with pytest.raises(llm.LLMError, match="Authentication failed"):
                llm.call_reviewer_llm("test", max_retries=3)
            assert mock_mod._mock_client.messages.create.call_count == 1
        finally:
            _cleanup_llm()

    def test_custom_model_override(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_mod = _make_mock_anthropic()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"decisions": []}')]
        mock_mod._mock_client.messages.create.return_value = mock_response
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)

        try:
            llm = _reload_with_anthropic(mock_mod)
            llm.call_reviewer_llm("test", model="my-custom-model")
            call_kwargs = mock_mod._mock_client.messages.create.call_args
            assert call_kwargs.kwargs["model"] == "my-custom-model"
        finally:
            _cleanup_llm()
