from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import datetime, timezone

from .config import (DATA_DIR, INPUT_DIR, METADATA_FILE, OUTPUT_DIR, ROOT, TRACE_FILE,
                     agent_models_metadata, agent_prompts_metadata)
from .data_store import OlistStore
from .orchestrator import DisputeOrchestrator
from .openrouter import OpenRouterClient
from .prompt_registry import validate_agent_registry


def load_local_env() -> None:
    """Load simple KEY=VALUE entries without adding a dotenv dependency."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve Olist disputes with auditable agents")
    parser.add_argument("--case", help="Run one case, e.g. EC_001")
    parser.add_argument("--offline", action="store_true", help="Disable OpenRouter inference")
    parser.add_argument("--require-openrouter", action="store_true",
                        help="Fail unless OPENROUTER_API_KEY is available")
    args = parser.parse_args()
    if args.offline and args.require_openrouter:
        parser.error("--offline and --require-openrouter are mutually exclusive")
    load_local_env()
    validate_agent_registry()
    client = None if args.offline else OpenRouterClient()
    if args.require_openrouter and not client.enabled:
        parser.error("OPENROUTER_API_KEY is required; put it in .env or the environment")
    llm_enabled = bool(client and client.enabled)
    OUTPUT_DIR.mkdir(exist_ok=True)
    TRACE_FILE.write_text("", encoding="utf-8")
    runner = DisputeOrchestrator(OlistStore(DATA_DIR), TRACE_FILE, client)
    paths = [INPUT_DIR / f"{args.case}.json"] if args.case else sorted(INPUT_DIR.glob("EC_*.json"))
    for path in paths:
        result = runner.run_case(path)
        (OUTPUT_DIR / path.name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata = {"provider": "OpenRouter", "agent_models": agent_models_metadata(),
                "agent_prompts": agent_prompts_metadata(),
                "openrouter_inference_enabled": llm_enabled,
                "framework": "custom typed multi-agent orchestration (Python stdlib)",
                "runtime": f"Python {platform.python_version()}", "cases_processed": len(paths),
                "generated_at": datetime.now(timezone.utc).isoformat()}
    METADATA_FILE.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Processed {len(paths)} case(s); outputs={OUTPUT_DIR}; trace={TRACE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
