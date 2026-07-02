# API documentation
import openai

client = openai.OpenAI(
    # The custom base URL points to W&B Inference
    base_url='https://api.inference.wandb.ai/v1',

    # Get an API key from https://wandb.ai/authorize
    # Consider setting it in the environment as OPENAI_API_KEY instead for safety
    api_key="<your-apikey>", (api_key already availebla in .env)

    # Optional: Team and project for usage tracking
    project="<team>/<project>",
)

response = client.chat.completions.create(
    model="nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Tell me a joke."}
    ],
)

print(response.choices[0].message.content)

# Model details
NVIDIA Nemotron 3 Ultra
Model overview
Price
$0.75 - $0.15 - $2.75
Input - Cached - Output
Parameters
55B - 550B
Active - Total
Context window
262k
 
Release date
Jun 2026
 
Nemotron 3 Ultra is a powerful MoE model designed by NVIDIA for long-running agents across coding, deep research, and enterprise automation. Built for efficiency, Nemotron 3 Ultra is ideal for deploying agentic workflows at scale.
NVIDIA
License: other
nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16
Supported features
Reasoning yes
JSON mode yes
Structured output yes
Tool calling yes 
LoRA No
Post training No