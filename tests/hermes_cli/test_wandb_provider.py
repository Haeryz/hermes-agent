"""Focused tests for W&B Inference provider wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.auxiliary_client import resolve_provider_client
from agent.model_metadata import get_model_context_length
from hermes_cli.auth import resolve_provider
from hermes_cli.models import (
    CANONICAL_PROVIDERS,
    _PROVIDER_LABELS,
    _PROVIDER_MODELS,
    normalize_provider,
    provider_model_ids,
)


@pytest.fixture(autouse=True)
def _clear_wandb_env(monkeypatch):
    for key in ("WANDB_API_KEY", "WANDB_BASE_URL", "WANDB_PROJECT"):
        monkeypatch.delenv(key, raising=False)


class TestWandbAliases:
    @pytest.mark.parametrize("alias", ["wandb", "wandb-inference", "weights-and-biases", "w-and-b"])
    def test_alias_resolves(self, alias, monkeypatch):
        monkeypatch.setenv("WANDB_API_KEY", "wandb-test-key")
        assert resolve_provider(alias) == "wandb"

    def test_models_normalize_provider(self):
        assert normalize_provider("wandb-inference") == "wandb"
        assert normalize_provider("weights-and-biases") == "wandb"
        assert normalize_provider("w-and-b") == "wandb"

    def test_providers_normalize_provider(self):
        from hermes_cli.providers import normalize_provider as normalize_provider_in_providers

        assert normalize_provider_in_providers("wandb-inference") == "wandb"
        assert normalize_provider_in_providers("weights-and-biases") == "wandb"
        assert normalize_provider_in_providers("w-and-b") == "wandb"


class TestWandbConfigRegistry:
    def test_optional_env_vars_include_wandb(self):
        from hermes_cli.config import OPTIONAL_ENV_VARS

        assert "WANDB_API_KEY" in OPTIONAL_ENV_VARS
        assert OPTIONAL_ENV_VARS["WANDB_API_KEY"]["category"] == "provider"
        assert OPTIONAL_ENV_VARS["WANDB_API_KEY"]["password"] is True
        assert OPTIONAL_ENV_VARS["WANDB_API_KEY"]["url"] == "https://wandb.ai/authorize"

        assert "WANDB_BASE_URL" in OPTIONAL_ENV_VARS
        assert OPTIONAL_ENV_VARS["WANDB_BASE_URL"]["category"] == "provider"
        assert OPTIONAL_ENV_VARS["WANDB_BASE_URL"]["password"] is False


class TestWandbModelCatalog:
    def test_canonical_provider_entry(self):
        slugs = [p.slug for p in CANONICAL_PROVIDERS]
        assert "wandb" in slugs
        assert _PROVIDER_LABELS["wandb"] == "W&B Inference"

    def test_static_catalog_includes_requested_model(self):
        assert _PROVIDER_MODELS["wandb"][0] == "deepseek-ai/DeepSeek-V4-Pro"
        assert "openai/gpt-oss-120b" in _PROVIDER_MODELS["wandb"]

    def test_provider_model_ids_prefers_live_api(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            lambda provider_id: {
                "provider": provider_id,
                "api_key": "wandb-live-key",
                "base_url": "https://api.inference.wandb.ai/v1",
                "source": "WANDB_API_KEY",
            },
        )
        monkeypatch.setattr(
            "providers.base.ProviderProfile.fetch_models",
            lambda self, api_key=None, timeout=8.0: [
                "deepseek-ai/DeepSeek-V4-Pro",
                "openai/gpt-oss-120b",
            ],
        )

        assert provider_model_ids("wandb") == [
            "deepseek-ai/DeepSeek-V4-Pro",
            "openai/gpt-oss-120b",
        ]

    def test_provider_model_ids_falls_back_to_static_models(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            lambda provider_id: {
                "provider": provider_id,
                "api_key": "wandb-live-key",
                "base_url": "https://api.inference.wandb.ai/v1",
                "source": "WANDB_API_KEY",
            },
        )
        monkeypatch.setattr(
            "providers.base.ProviderProfile.fetch_models",
            lambda self, api_key=None, timeout=8.0: None,
        )

        assert provider_model_ids("wandb") == list(_PROVIDER_MODELS["wandb"])


class TestWandbProvidersModule:
    def test_overlay_exists(self):
        from hermes_cli.providers import HERMES_OVERLAYS

        assert "wandb" in HERMES_OVERLAYS
        overlay = HERMES_OVERLAYS["wandb"]
        assert overlay.transport == "openai_chat"
        assert overlay.extra_env_vars == ("WANDB_API_KEY",)
        assert overlay.base_url_override == "https://api.inference.wandb.ai/v1"
        assert overlay.base_url_env_var == "WANDB_BASE_URL"
        assert overlay.is_aggregator


class TestWandbProfile:
    def test_profile_declares_wandb_endpoint(self):
        from providers import get_provider_profile

        profile = get_provider_profile("wandb")
        assert profile is not None
        assert profile.base_url == "https://api.inference.wandb.ai/v1"
        assert profile.default_aux_model == "deepseek-ai/DeepSeek-V4-Pro"

    def test_project_config_maps_to_openai_project_header(self, monkeypatch):
        from providers import get_provider_profile

        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"wandb": {"project": "team/project"}},
        )

        profile = get_provider_profile("wandb")
        assert profile is not None
        _, top_level = profile.build_api_kwargs_extras()

        assert top_level == {"extra_headers": {"OpenAI-Project": "team/project"}}

    def test_project_env_fallback_maps_to_openai_project_header(self, monkeypatch):
        from providers import get_provider_profile

        monkeypatch.setenv("WANDB_PROJECT", "env-team/env-project")

        profile = get_provider_profile("wandb")
        assert profile is not None
        _, top_level = profile.build_api_kwargs_extras()

        assert top_level == {
            "extra_headers": {"OpenAI-Project": "env-team/env-project"}
        }


class TestWandbAuxiliary:
    def test_resolve_provider_client_uses_wandb_aux_default(self, monkeypatch):
        monkeypatch.setenv("WANDB_API_KEY", "wandb-test-key")

        with patch("agent.auxiliary_client.OpenAI") as mock_openai:
            mock_openai.return_value = object()
            client, model = resolve_provider_client("wandb")

        assert client is not None
        assert model == "deepseek-ai/DeepSeek-V4-Pro"
        assert mock_openai.call_args.kwargs["api_key"] == "wandb-test-key"
        assert (
            mock_openai.call_args.kwargs["base_url"]
            == "https://api.inference.wandb.ai/v1"
        )


class TestWandbModelMetadata:
    def test_url_to_provider(self):
        from agent.model_metadata import _URL_TO_PROVIDER

        assert _URL_TO_PROVIDER.get("api.inference.wandb.ai") == "wandb"

    def test_provider_prefixes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES

        assert "wandb" in _PROVIDER_PREFIXES
        assert "wandb-inference" in _PROVIDER_PREFIXES
        assert "weights-and-biases" in _PROVIDER_PREFIXES

    def test_known_wandb_endpoint_still_uses_endpoint_metadata(self):
        with patch(
            "agent.model_metadata.get_cached_context_length",
            return_value=None,
        ), patch(
            "agent.model_metadata.fetch_endpoint_model_metadata",
            return_value={"acme/unknown-wandb-model": {"context_length": 131072}},
        ), patch(
            "agent.models_dev.lookup_models_dev_context",
            return_value=None,
        ), patch(
            "agent.model_metadata.fetch_model_metadata",
            return_value={},
        ):
            result = get_model_context_length(
                "acme/unknown-wandb-model",
                base_url="https://api.inference.wandb.ai/v1",
                api_key="wandb-test-key",
                provider="custom",
            )

        assert result == 131072
