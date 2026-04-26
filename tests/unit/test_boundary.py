import numpy as np

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.graph import GraphContainer, Transition
from dam.boundary.list_container import ListContainer
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer


def make_node(node_id: str = "n0") -> BoundaryNode:
    return BoundaryNode(
        node_id=node_id,
        constraint=BoundaryConstraint(
            params={
                "upper": np.ones(6).tolist(),
                "lower": (-np.ones(6)).tolist(),
            }
        ),
    )


def test_constraint_params_stored():
    params = {"max_speed": 0.5, "bounds": [[0, 1], [0, 1], [0, 1]]}
    c = BoundaryConstraint(params=params)
    assert abs(c.params["max_speed"] - 0.5) < 1e-9
    assert c.params["bounds"] == [[0, 1], [0, 1], [0, 1]]


def test_single_container_always_same_node():
    node = make_node()
    container = SingleNodeContainer(node)
    container.advance()
    container.advance()
    assert container.get_active_node() is node


def test_single_container_snapshot_restore():
    node = make_node()
    container = SingleNodeContainer(node)
    state = container.snapshot()
    container.restore(state)
    assert container.get_active_node() is node


def test_list_container_advance():
    nodes = [make_node(f"n{i}") for i in range(3)]
    container = ListContainer(nodes)
    assert container.get_active_node().node_id == "n0"
    container.advance()
    assert container.get_active_node().node_id == "n1"
    container.advance()
    assert container.get_active_node().node_id == "n2"
    container.advance()  # At end, stays
    assert container.get_active_node().node_id == "n2"


def test_list_container_reset():
    nodes = [make_node(f"n{i}") for i in range(3)]
    container = ListContainer(nodes)
    container.advance()
    container.advance()
    container.reset()
    assert container.get_active_node().node_id == "n0"


def test_list_container_loop():
    nodes = [make_node(f"n{i}") for i in range(2)]
    container = ListContainer(nodes, loop=True)
    container.advance()
    assert container.get_active_node().node_id == "n1"
    container.advance()  # Loops back
    assert container.get_active_node().node_id == "n0"


def test_list_container_snapshot_restore():
    nodes = [make_node(f"n{i}") for i in range(3)]
    container = ListContainer(nodes)
    container.advance()
    state = container.snapshot()
    container.advance()
    container.restore(state)
    assert container.get_active_node().node_id == "n1"


def test_graph_container_transition():
    nodes = {
        "a": make_node("a"),
        "b": make_node("b"),
    }
    transitions = {
        "a": [Transition(to_node="b", condition=lambda: True)],
    }
    container = GraphContainer(nodes, transitions, "a")
    assert container.get_active_node().node_id == "a"
    container.advance()
    assert container.get_active_node().node_id == "b"


def test_graph_container_conditional_transition():
    flag = {"go": False}
    nodes = {"a": make_node("a"), "b": make_node("b"), "c": make_node("c")}
    transitions = {
        "a": [
            Transition(to_node="b", condition=lambda: flag["go"], priority=1),
            Transition(to_node="c", condition=None, priority=0),
        ]
    }
    container = GraphContainer(nodes, transitions, "a")
    container.advance()
    assert container.get_active_node().node_id == "c"  # flag["go"] is False

    container = GraphContainer(nodes, transitions, "a")
    flag["go"] = True
    container.advance()
    assert container.get_active_node().node_id == "b"  # flag["go"] is True


def test_graph_container_snapshot_restore():
    nodes = {"a": make_node("a"), "b": make_node("b")}
    transitions = {"a": [Transition(to_node="b")]}
    container = GraphContainer(nodes, transitions, "a")
    state = container.snapshot()
    container.advance()
    assert container.get_active_node().node_id == "b"
    container.restore(state)
    assert container.get_active_node().node_id == "a"
