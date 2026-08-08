"""Close the truncation loop for replacement models in the V5 runtime.

Continuous spatiotemporal replanning already learns a node-local output
allowance floor from every attempt.  This layer makes that feedback actionable
immediately when *a replacement model itself* truncates: before selecting a
new model, retry the same replacement model once with the newly learned current-
run request shape.  The graph and candidate space remain finite and no state is
kept across tasks.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_continuous_spatiotemporal_replanning import (
    ContinuousSpatiotemporalExecutionEngine,
)
from v5_runtime import FailureCategory, ProductionRuntime


class ReplacementTruncationRebindExecutionEngine(
    ContinuousSpatiotemporalExecutionEngine
):
    """Retry a truncated replacement once before cross-model substitution."""

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        def feedback_aware_call(candidate: SelectedNode, attempt_kind: str) -> Any:
            attempted = call(candidate, attempt_kind)
            if (
                attempted is None
                or attempt_kind != "replacement"
                or self._category(attempted) != FailureCategory.OUTPUT_TRUNCATED
            ):
                return attempted

            # The first truncated attempt has already been observed by the
            # normal call path, so _replacement_adaptation sees the newly
            # learned node-local allowance floor before this retry is bound.
            learned_candidate, adaptation = self._replacement_adaptation(
                candidate,
                attempted,
                False,
            )
            dynamic_window = int(
                getattr(self, "_last_spatiotemporal_batch", {}).get(
                    "effective_dynamic_depth",
                    0,
                )
            )
            self._record_continuous_replan_event(
                selected=selected,
                candidate=candidate,
                source_kind="replacement-truncation-observed",
                category_before=FailureCategory.OUTPUT_TRUNCATED,
                attempted=attempted,
                initial_remaining=len(recovery_rows),
                standby_window=dynamic_window,
            )

            retried = call(learned_candidate, "retry")
            if retried is None:
                return attempted
            if adaptation is not None:
                retried.answer_transformations.append(adaptation)
            self._same_model_truncation_retries.append(
                {
                    "model": candidate.model,
                    "source_attempt_index": int(
                        getattr(attempted, "attempt_index", 0)
                    ),
                    "retry_attempt_index": int(
                        getattr(retried, "attempt_index", 0)
                    ),
                    "status": str(getattr(retried, "status", "")),
                    "policy": (
                        "replacement-same-model-feedback-rebind-before-"
                        "cross-model-substitution"
                    ),
                    "current_run_only": True,
                }
            )
            self._record_continuous_replan_event(
                selected=selected,
                candidate=learned_candidate,
                source_kind="same-model-retry-after-replacement-truncation",
                category_before=FailureCategory.OUTPUT_TRUNCATED,
                attempted=retried,
                initial_remaining=len(recovery_rows),
                standby_window=dynamic_window,
            )
            return retried

        return super()._recover_node(
            selected,
            attempts,
            recovery_rows,
            category,
            best,
            feedback_aware_call,
        )

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        value.update(
            {
                "replacement_truncation_same_model_rebind_enabled": True,
                "replacement_truncation_same_model_rebind_limit": 1,
                "replacement_truncation_cross_model_substitution_order": (
                    "same-model-current-run-feedback-rebind-first"
                ),
            }
        )
        return value


def install_replacement_truncation_rebind(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    """Install the final production engine after continuous replanning setup."""
    runtime.execution_engine = ReplacementTruncationRebindExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "ReplacementTruncationRebindExecutionEngine",
    "install_replacement_truncation_rebind",
]
