"""Weights & Biases Inference provider profile."""

from __future__ import annotations

import os
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


def _configured_project() -> str:
    """Return optional W&B usage-tracking project as ``team/project``."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        wandb_cfg = cfg.get("wandb", {}) if isinstance(cfg, dict) else {}
        project = wandb_cfg.get("project", "") if isinstance(wandb_cfg, dict) else ""
        if isinstance(project, str) and project.strip():
            return project.strip()
    except Exception:
        pass
    return os.getenv("WANDB_PROJECT", "").strip()


class WandbInferenceProfile(ProviderProfile):
    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project = _configured_project()
        if not project:
            return {}, {}
        return {}, {"extra_headers": {"OpenAI-Project": project}}


wandb = WandbInferenceProfile(
    name="wandb",
    aliases=("wandb-inference", "weights-and-biases", "w-and-b"),
    display_name="W&B Inference",
    description="W&B Inference - OpenAI-compatible hosted models",
    signup_url="https://wandb.ai/authorize",
    env_vars=("WANDB_API_KEY", "WANDB_BASE_URL"),
    base_url="https://api.inference.wandb.ai/v1",
    auth_type="api_key",
    default_aux_model="deepseek-ai/DeepSeek-V4-Pro",
    fallback_models=(
        "deepseek-ai/DeepSeek-V4-Pro",
        "openai/gpt-oss-120b",
    ),
)

register_provider(wandb)
