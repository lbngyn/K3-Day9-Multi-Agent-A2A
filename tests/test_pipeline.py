import json
import tempfile
import unittest
from pathlib import Path

from src.config import DATA_DIR, INPUT_DIR
from src.config import AGENT_MODEL_CONFIG, get_agent_model_config
from src.data_store import OlistStore
from src.orchestrator import DisputeOrchestrator
from src.openrouter import OpenRouterClient
from src.prompt_registry import get_agent_system_prompt, validate_agent_registry


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = OlistStore(DATA_DIR)

    def test_all_official_cases_are_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = DisputeOrchestrator(self.store, Path(directory) / "trace.jsonl")
            results = [runner.run_case(path) for path in sorted(INPUT_DIR.glob("EC_*.json"))]
        self.assertEqual(50, len(results))
        self.assertEqual(50, len({r["case_id"] for r in results}))

    def test_json_serializable(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = DisputeOrchestrator(self.store, Path(directory) / "trace.jsonl")
            json.dumps(runner.run_case(INPUT_DIR / "EC_001.json"))

    def test_every_agent_model_respects_parameter_limit(self):
        expected = {"coordinator_agent", "order_seller_agent", "payment_agent",
                    "delivery_agent", "policy_agent"}
        self.assertEqual(expected, set(AGENT_MODEL_CONFIG))
        for agent_id in expected:
            self.assertLessEqual(get_agent_model_config(agent_id)["parameter_size_billion"], 10)

    def test_every_agent_has_soul_and_system_prompt(self):
        validate_agent_registry()
        for agent_id in AGENT_MODEL_CONFIG:
            prompt = get_agent_system_prompt(agent_id)
            self.assertIn("SOUL", prompt)
            self.assertIn("SYSTEM INSTRUCTIONS", prompt)

    def test_model_client_is_wired_to_all_agents(self):
        class FakeClient:
            enabled = True

            def __init__(self):
                self.calls = []

            def complete_json(self, agent_id, system, payload):
                self.calls.append(agent_id)
                if agent_id == "coordinator_agent":
                    completed = set(payload["state"]["completed_agents"])
                    for target in ["order_seller_agent", "payment_agent", "delivery_agent",
                                   "policy_agent"]:
                        if target not in completed:
                            return {"action": "delegate", "target_agent": target,
                                    "task": "collect evidence"}
                    return {"action": "finalize", "reason": "evidence complete"}
                return {"mock_review": True}

        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            runner = DisputeOrchestrator(
                self.store, Path(directory) / "trace.jsonl", client
            )
            runner.run_case(INPUT_DIR / "EC_001.json")
        self.assertEqual(set(AGENT_MODEL_CONFIG), set(client.calls))

    def test_openrouter_extracts_tool_arguments_when_content_is_null(self):
        result = {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None,
            "tool_calls": [{"function": {"arguments": '{"action":"finalize"}'}}],
        }}]}
        self.assertEqual(
            {"action": "finalize"},
            OpenRouterClient._extract_json(result, "coordinator_agent"),
        )

    def test_openrouter_null_content_has_diagnostic_error(self):
        result = {"choices": [{"finish_reason": "length", "message": {
            "content": None, "reasoning": "unfinished",
        }}]}
        with self.assertRaisesRegex(ValueError, "finish_reason='length'"):
            OpenRouterClient._extract_json(result, "coordinator_agent")

    def test_openrouter_can_recover_json_from_reasoning_field(self):
        result = {"choices": [{"finish_reason": "length", "message": {
            "content": None,
            "reasoning": 'Short analysis. Final: {"agents":["payment_agent"]}',
        }}]}
        self.assertEqual(
            {"agents": ["payment_agent"]},
            OpenRouterClient._extract_json(result, "coordinator_agent"),
        )

    def test_specialists_receive_scoped_data_not_full_case(self):
        scopes = self.store.context(INPUT_DIR / "EC_001.json")
        self.assertFalse(hasattr(scopes.order_seller, "payments"))
        self.assertFalse(hasattr(scopes.payment, "items"))
        self.assertFalse(hasattr(scopes.payment, "order_status"))
        self.assertFalse(hasattr(scopes.delivery, "payments"))
        self.assertFalse(hasattr(scopes.delivery, "items"))
        self.assertFalse(hasattr(scopes.header, "order"))

    def test_second_route_error_activates_fallback_instead_of_failing(self):
        class BadRouter:
            enabled = True

            def complete_json(self, agent_id, system, payload):
                if agent_id == "coordinator_agent":
                    return {"action": "delegate", "target_agent": "policy_agent",
                            "task": "apply too early"}
                return {"review": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            runner = DisputeOrchestrator(
                self.store, trace_path, BadRouter()
            )
            result = runner.run_case(INPUT_DIR / "EC_001.json")
            self.assertEqual("EC_001", result["case_id"])
            self.assertIn('"event": "fallback_activated"', trace_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
