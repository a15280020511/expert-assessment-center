# Full Repository Audit

- Files: `140`
- Lines inspected: `27316`
- Critical: `0`
- High: `0`
- Medium: `41`
- Low: `0`
- Info: `0`

| Severity | Rule | File | Line | Finding |
|---|---|---|---:|---|
| medium | `PY-COMPLEXITY` | `open-model-market/execution_graph_validator.py` | 110 | function 'validate_execution_graph' complexity=69 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/execution_graph_validator.py` | 110 | function 'validate_execution_graph' spans 399 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/model_market.py` | 188 | function 'fetch_catalog' complexity=29 |
| medium | `PY-COMPLEXITY` | `open-model-market/openrouter_api.py` | 173 | function '_merge_streaming_choices' complexity=24 |
| medium | `PY-COMPLEXITY` | `open-model-market/publish_report.py` | 141 | function 'strict_publication_gate' complexity=25 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_catalog_view.py` | 157 | function 'compact_endpoint_catalog' complexity=27 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_claude_red_team_policy.py` | 151 | function '_validate_payload' complexity=29 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_constitutional_runtime.py` | 296 | function '_actual_company_audit' complexity=29 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_constitutional_runtime.py` | 415 | function 'execute_graph' complexity=27 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_evidence_bundle.py` | 121 | function 'render_final_status_markdown' complexity=30 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_execution_auditor.py` | 41 | function 'audit' complexity=159 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_execution_auditor.py` | 41 | function 'audit' spans 492 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_execution_auditor_integrity.py` | 62 | function '_node_quality' complexity=25 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_execution_auditor_integrity.py` | 125 | function '_apply_native_contract' complexity=22 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_execution_auditor_integrity.py` | 294 | function 'audit' complexity=27 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_gpt_expert_selector.py` | 375 | function '_validate_proposal' complexity=53 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_independent_artifact_revalidation.py` | 55 | function '_manifest_checks' complexity=25 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_independent_artifact_revalidation.py` | 298 | function 'recompute' complexity=70 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_independent_artifact_revalidation.py` | 298 | function 'recompute' spans 252 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_issue_ticket.py` | 171 | function 'duplicate_reason' complexity=30 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_issue_ticket.py` | 253 | function '_format_schema_error' complexity=36 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_issue_ticket.py` | 486 | function 'prepare' complexity=33 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_pipeline.py` | 202 | function 'main' spans 287 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_proposal_materializer.py` | 286 | function 'materialize_proposal' complexity=49 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_proposal_materializer.py` | 286 | function 'materialize_proposal' spans 212 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_proposal_materializer.py` | 530 | function 'claude_unified_review_payload' complexity=24 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_quality_status_integrity.py` | 46 | function 'enforce_result_integrity' complexity=24 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_run_evidence.py` | 146 | function 'build' complexity=55 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_run_evidence.py` | 146 | function 'build' spans 333 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_runtime.py` | 777 | function '_attempt' complexity=29 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_runtime.py` | 970 | function 'execute_node' complexity=33 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_runtime.py` | 1274 | function 'execute_graph' complexity=72 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_runtime.py` | 1274 | function 'execute_graph' spans 256 lines |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_task_constraints_impl.py` | 573 | function '_claim_supported' complexity=25 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_task_delivery_contract_impl.py` | 449 | function 'validate_parsed_contract' complexity=29 |
| medium | `PY-COMPLEXITY` | `open-model-market/v5_ticket_gate.py` | 68 | function 'validate_gate' complexity=28 |
| medium | `PY-FUNCTION-SIZE` | `open-model-market/v5_ticket_gate.py` | 68 | function 'validate_gate' spans 286 lines |
| medium | `PY-FUNCTION-SIZE` | `tests/test_v5_native_audit_contract.py` | 28 | function '_fixture' spans 234 lines |
| medium | `PY-COMPLEXITY` | `tools/repository_audit.py` | 102 | function 'audit_python' complexity=43 |
| medium | `PY-COMPLEXITY` | `tools/repository_audit.py` | 253 | function 'audit' complexity=58 |
| medium | `PY-FUNCTION-SIZE` | `tools/repository_audit.py` | 253 | function 'audit' spans 229 lines |
