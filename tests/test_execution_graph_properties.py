import sys
from pathlib import Path

from hypothesis import given, strategies as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph, SelectedEdge, SelectedNode  # noqa: E402
from execution_graph_validator import derive_execution_stages, validate_execution_graph  # noqa: E402


def make_node(index):
    return SelectedNode(
        node_id=f"node-{index}",
        assigned_work=(f"work-{index}",),
        professional_capabilities={"analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": ["scope_control"]},
        reasoning_profile={"strategy": "analysis"},
        parameter_profile={"temperature": 0.05},
        model=f"vendor-{index}/model-{index}",
        provider_endpoint=f"provider-{index}",
        output_contract={"type": "object"},
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.01,
        request_config={"messages": []},
    )


@st.composite
def dag_graphs(draw):
    size = draw(st.integers(min_value=1, max_value=8))
    nodes = tuple(make_node(index) for index in range(size))
    possible = [(source, target) for source in range(size) for target in range(source + 1, size)]
    chosen = draw(st.sets(st.sampled_from(possible), max_size=len(possible))) if possible else set()
    edges = tuple(
        SelectedEdge(
            source=f"node-{source}",
            target=f"node-{target}",
            relation_type="dependency",
            payload_type="structured_conclusion",
            visibility_policy="allow",
        )
        for source, target in sorted(chosen)
    )
    provisional = ExecutionGraph(
        nodes=nodes,
        edges=edges,
        execution_stages=(),
        entry_nodes=(),
        final_nodes=(),
        required_work=tuple(f"work-{index}" for index in range(size)),
        estimated_quality=0.8,
        quality_floor=0.75,
        estimated_total_cost=0.01 * size,
        metadata={"version": 5},
    )
    stages = derive_execution_stages(provisional)
    indegree = {node.node_id: 0 for node in nodes}
    outdegree = {node.node_id: 0 for node in nodes}
    for edge in edges:
        indegree[edge.target] += 1
        outdegree[edge.source] += 1
    return ExecutionGraph(
        nodes=nodes,
        edges=edges,
        execution_stages=stages,
        entry_nodes=tuple(sorted(node_id for node_id, degree in indegree.items() if degree == 0)),
        final_nodes=tuple(sorted(node_id for node_id, degree in outdegree.items() if degree == 0)),
        required_work=provisional.required_work,
        estimated_quality=provisional.estimated_quality,
        quality_floor=provisional.quality_floor,
        estimated_total_cost=provisional.estimated_total_cost,
        metadata=provisional.metadata,
    )


@given(dag_graphs())
def test_generated_forward_graphs_always_validate(graph):
    assert validate_execution_graph(graph) == ()


@given(st.integers(min_value=2, max_value=8))
def test_back_edge_is_always_detected_as_cycle(size):
    nodes = tuple(make_node(index) for index in range(size))
    edges = tuple(
        SelectedEdge(
            source=f"node-{index}",
            target=f"node-{index + 1}",
            relation_type="dependency",
            payload_type="structured_conclusion",
            visibility_policy="allow",
        )
        for index in range(size - 1)
    ) + (
        SelectedEdge(
            source=f"node-{size - 1}",
            target="node-0",
            relation_type="correction",
            payload_type="structured_conclusion",
            visibility_policy="allow",
        ),
    )
    graph = ExecutionGraph(
        nodes=nodes,
        edges=edges,
        execution_stages=tuple((f"node-{index}",) for index in range(size)),
        entry_nodes=(),
        final_nodes=(),
        required_work=tuple(f"work-{index}" for index in range(size)),
        estimated_quality=0.8,
        quality_floor=0.75,
        estimated_total_cost=0.01 * size,
        metadata={"version": 5},
    )
    assert "cycle" in {issue.code for issue in validate_execution_graph(graph)}
