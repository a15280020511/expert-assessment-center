import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, GraphLimits  # noqa: E402
from execution_graph_validator import validate_execution_graph  # noqa: E402
from resource_matrix import compile_v5_task_resources  # noqa: E402
from v5_benchmark import planning_benchmark  # noqa: E402
from v5_executor import V5ExecutionError, execute_v5_graph  # noqa: E402
from v5_planner import compile_and_optimize_v5  # noqa: E402


class TestV5PlannerExecutor(unittest.TestCase):
    @staticmethod
    def profile():
        return SimpleNamespace(
            domains=["business", "legal"],
            primary_domain="business",
            secondary_domain="legal",
            complexity="complex",
            complexity_score=6,
            high_stakes=True,
            chinese=True,
            long_context=False,
            requested_context=32768,
        )

    @staticmethod
    def run(task):
        return SimpleNamespace(
            task=task,
            parallel_workers=4,
            api_key=None,
            model_timeout_seconds=30,
            model_max_retries=0,
        )

    @staticmethod
    def models():
        def model(model_id, description, rank, prompt, completion, supported=("reasoning", "structured_outputs")):
            return SimpleNamespace(
                id=model_id,
                name=model_id,
                description=description,
                author=model_id.split("/", 1)[0],
                context_length=131072,
                max_completion_tokens=16000,
                prompt_price_per_million=prompt,
                completion_price_per_million=completion,
                supported_parameters=list(supported),
                input_modalities=["text"],
                output_modalities=["text"],
                reasoning={"enabled": "reasoning" in supported},
                ranks={"intelligence-high-to-low": rank},
                components={},
            )

        return [
            model("alpha/prime", "advanced reasoning mathematics research evidence business coding", 1, 8.0, 24.0),
            model("beta/value", "business finance economics investment strategy analysis research", 3, 2.0, 6.0),
            model("kappa/risk", "legal compliance security safety audit risk adversarial review", 4, 3.5, 10.0),
            model("theta/code", "software coding implementation engineering security repository", 5, 4.0, 12.0),
            model("delta/research", "long context research evidence policy documents reasoning", 6, 3.0, 9.0),
            model("gamma/general", "general analysis reasoning decision writing assistant", 8, 0.5, 1.5, ("reasoning",)),
        ]

    @classmethod
    def endpoints(cls):
        payloads = {}
        for index, model in enumerate(cls.models()):
            payloads[model.id] = {
                "data": {
                    "endpoints": [{
                        "tag": f"provider-{index}",
                        "context_length": model.context_length,
                        "max_completion_tokens": model.max_completion_tokens,
                        "pricing": {
                            "prompt": model.prompt_price_per_million,
                            "completion": model.completion_price_per_million,
                        },
                        "supported_parameters": model.supported_parameters,
                        "uptime": 0.99 - index * 0.005,
                    }]
                }
            }
        return payloads

    def planner(self):
        run = self.run("比较城市公共投资方案，进行财务建模、法律合规、证据核验、预测、风险反证并给出最终决策。")
        resources = compile_v5_task_resources(self.profile(), run)
        limits = GraphLimits(
            max_nodes=16,
            max_edges=64,
            max_stages=8,
            max_model_calls=16,
            max_retries=0,
            max_replacements=2,
        )
        return compile_and_optimize_v5(
            self.models(),
            resources,
            endpoint_payloads=self.endpoints(),
            ranking_limit=50,
            limits=limits,
            maximum_per_group=10,
            solver_timeout_seconds=10,
        )

    def test_real_endpoint_market_and_joint_graph_are_valid(self):
        planner = self.planner()
        self.assertGreater(planner["market"]["real_endpoint_count"], 0)
        self.assertEqual(planner["market"]["synthetic_fixture_count"], 0)
        self.assertGreaterEqual(
            planner["candidate_graph"]["candidate_count_before_pareto"],
            planner["candidate_graph"]["candidate_count_after_pareto"],
        )
        self.assertGreater(planner["candidate_graph"]["candidate_count_after_pareto"], 0)
        graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])
        self.assertFalse(validate_execution_graph(graph, GraphLimits()))
        self.assertLessEqual(len(graph.nodes), 16)
        self.assertTrue(all(node.request_config["provider"]["allow_fallbacks"] is False for node in graph.nodes))
        self.assertTrue(all(node.request_config["provider"]["only"] for node in graph.nodes))
        encoded = json.dumps(graph.to_dict(), ensure_ascii=False)
        for forbidden in ("tools", "tool_choice", "web_search", '"models"', "openrouter/auto", ":online", ":batch"):
            self.assertNotIn(forbidden, encoded)

    def test_independent_copies_use_distinct_models_and_endpoints(self):
        graph = ExecutionGraph.from_mapping(self.planner()["optimization"]["execution_graph"])
        groups = {}
        for node in graph.nodes:
            if node.independence_group:
                groups.setdefault(node.independence_group, []).append(node)
        self.assertTrue(any(len(rows) >= 2 for rows in groups.values()))
        for rows in groups.values():
            if len(rows) < 2:
                continue
            self.assertEqual(len(rows), len({row.model for row in rows}))
            self.assertEqual(len(rows), len({row.provider_endpoint for row in rows}))

    @staticmethod
    def complete_answer():
        required_words = " ".join([
            "conclusions assumptions uncertainties evidence_gaps variables formulas calculations sensitivity",
            "scenarios triggers forecast_horizon failure_modes counterexamples rejection_conditions",
            "options criteria tradeoffs ranking agreements disagreements conflict_resolution final_recommendation",
            "validated_claims unsupported_claims verification_limits dependencies steps acceptance_tests rollback_conditions",
        ])
        return (required_words + "。这是完整、结构化、可交付的节点结果，明确区分事实、假设、推断和不确定性。") * 8

    def test_layered_executor_recovers_with_a_different_candidate(self):
        planner = self.planner()
        graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])
        state = {"failed_once": False}
        answer = self.complete_answer()

        def fake_call(run, payload):
            if not state["failed_once"]:
                state["failed_once"] = True
                raise RuntimeError("simulated endpoint failure")
            return {
                "id": "resp-test",
                "model": payload["model"],
                "provider": payload["provider"]["order"][0],
                "choices": [{"finish_reason": "stop", "message": {"content": answer}}],
                "usage": {"cost": 0.001},
            }, 0.01

        with tempfile.TemporaryDirectory() as temp:
            result = execute_v5_graph(
                graph,
                self.run("测试任务"),
                "测试任务",
                call_fn=fake_call,
                output_dir=temp,
                limits=GraphLimits(max_retries=0, max_replacements=2),
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["recovery_used"])
            self.assertLessEqual(result["execution_budget"]["replacements_reserved"], 2)
            self.assertTrue((Path(temp) / "v5-final-report.md").exists())
            audit = json.loads((Path(temp) / "v5-request-audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "PASS")
            self.assertFalse(audit["artificial_token_ceiling_sent"])

    def test_replacement_limit_is_shared_by_the_whole_graph(self):
        graph = ExecutionGraph.from_mapping(self.planner()["optimization"]["execution_graph"])

        def always_fail(run, payload):
            raise RuntimeError("all endpoints fail")

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(V5ExecutionError):
                execute_v5_graph(
                    graph,
                    self.run("测试任务"),
                    "测试任务",
                    call_fn=always_fail,
                    output_dir=temp,
                    limits=GraphLimits(max_retries=0, max_replacements=2),
                )
            summary = json.loads((Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8"))
            budget = summary["execution_budget"]
            self.assertLessEqual(budget["replacements_reserved"], 2)
            self.assertLessEqual(budget["calls_reserved"], len(graph.nodes) + 2)
            self.assertTrue(any(row["reason"] == "global-replacement-limit-exhausted" for row in budget["denials"]))

    def test_planning_diagnostic_is_v5_only(self):
        benchmark = planning_benchmark(self.planner())
        self.assertTrue(benchmark["planning_gate_passed"])
        self.assertEqual(benchmark["runtime_policy"], "v5-only-no-alternate-runtime")
        self.assertEqual(
            set(benchmark["strategies"]),
            {
                "v5_joint_graph",
                "strongest_single_model",
                "lowest_price_single_model",
                "random_feasible",
                "lowest_cost_feasible",
            },
        )
        self.assertFalse(benchmark["strategies"]["strongest_single_model"]["feasible"])
        self.assertTrue(benchmark["strategies"]["strongest_single_model"]["hard_constraint_violations"])


if __name__ == "__main__":
    unittest.main()
