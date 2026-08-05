from pathlib import Path
from types import MappingProxyType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
TRACE_FILE = ROOT / "trace.jsonl"
METADATA_FILE = ROOT / "metadata.json"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model names deliberately live in source (not .env) for audit/grading.
# Each key must match the agent's ``name`` attribute.
AGENT_MODEL_CONFIG = MappingProxyType({
    "coordinator_agent": {
        "model": "qwen/qwen3.5-9b",
        "parameter_size_billion": 9.0,
        "temperature": 0.0,
        "max_tokens": 1200,
        "reasoning_enabled": False,
    },
    "order_seller_agent": {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "parameter_size_billion": 8.0,
        "temperature": 0.0,
        "max_tokens": 350,
        "reasoning_enabled": False,
    },
    "payment_agent": {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "parameter_size_billion": 8.0,
        "temperature": 0.0,
        "max_tokens": 350,
        "reasoning_enabled": False,
    },
    "delivery_agent": {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "parameter_size_billion": 8.0,
        "temperature": 0.0,
        "max_tokens": 350,
        "reasoning_enabled": False,
    },
    "policy_agent": {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "parameter_size_billion": 8.0,
        "temperature": 0.0,
        "max_tokens": 500,
        "reasoning_enabled": False,
    },
    "verifier_agent": {
        "model": "meta-llama/llama-3.1-8b-instruct",
        "parameter_size_billion": 8.0,
        "temperature": 0.0,
        "max_tokens": 350,
        "reasoning_enabled": False,
    },
})

# Persona/prompt mapping is independent from model mapping, so either can be
# changed without touching agent implementation.
AGENT_PROMPT_CONFIG = MappingProxyType({
    "coordinator_agent": {"soul": "Evidence-first case lead", "system_prompt_file": "coordinator.md"},
    "order_seller_agent": {"soul": "Skeptical order and seller investigator", "system_prompt_file": "order_seller.md"},
    "payment_agent": {"soul": "Exact payment reconciliation accountant", "system_prompt_file": "payment.md"},
    "delivery_agent": {"soul": "Timestamp-focused delivery investigator", "system_prompt_file": "delivery.md"},
    "policy_agent": {"soul": "Conservative EC_POLICY_V1 adjudicator", "system_prompt_file": "policy.md"},
    "verifier_agent": {"soul": "Adversarial evidence and schema auditor", "system_prompt_file": "verifier.md"},
})


def get_agent_model_config(agent_id: str) -> dict[str, Any]:
    """Return a defensive copy of one agent's OpenRouter configuration."""
    try:
        config = AGENT_MODEL_CONFIG[agent_id]
    except KeyError as exc:
        known = ", ".join(sorted(AGENT_MODEL_CONFIG))
        raise KeyError(f"No model configured for agent '{agent_id}'. Known agents: {known}") from exc
    if config["parameter_size_billion"] > 10:
        raise ValueError(f"Agent '{agent_id}' uses a model larger than the 10B limit")
    return dict(config)


def agent_models_metadata() -> dict[str, dict[str, Any]]:
    """Serializable agent-to-model mapping for metadata.json."""
    return {
        agent_id: {
            "model": config["model"],
            "parameter_size_billion": config["parameter_size_billion"],
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
            "reasoning_enabled": config["reasoning_enabled"],
        }
        for agent_id, config in AGENT_MODEL_CONFIG.items()
    }


def agent_prompts_metadata() -> dict[str, dict[str, str]]:
    """Serializable soul/prompt mapping for metadata and audits."""
    return {agent_id: dict(config) for agent_id, config in AGENT_PROMPT_CONFIG.items()}
