"""Weights & Biases (W&B) Inference provider profile."""

from providers import register_provider
from providers.base import ProviderProfile

wandb = ProviderProfile(
    name="wandb",
    aliases=("wandb-inference", "weave", "weights-and-biases"),
    display_name="W&B Inference",
    description="Weights & Biases Inference — hosted open models (OpenAI-compatible)",
    signup_url="https://wandb.ai/authorize",
    env_vars=("WANDB_API_KEY", "WANDB_BASE_URL"),
    base_url="https://api.inference.wandb.ai/v1",
    auth_type="api_key",
    # NVIDIA Nemotron 3 Ultra ships a 262k context window; keep a sane
    # completion cap so we don't request the full window by default.
    default_max_tokens=16384,
    default_aux_model="meta-llama/Llama-3.1-8B-Instruct",
    fallback_models=(
        "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B",
        "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8",
        "moonshotai/Kimi-K2.7-Code",
        "deepseek-ai/DeepSeek-V4-Pro",
        "Qwen/Qwen3-Coder-480B-A35B-Instruct",
        "zai-org/GLM-5.2",
        "openai/gpt-oss-120b",
        "meta-llama/Llama-3.3-70B-Instruct",
    ),
)

register_provider(wandb)
