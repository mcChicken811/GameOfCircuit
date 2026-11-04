"""Interactive boolean-node grid. Run with: .venv/bin/python circuit_grid.py"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Iterator


BINARY_GATE_KINDS = ("AND", "OR", "NAND", "NOR")


@dataclass(eq=False)
class Node:
    """A node and its actual, mutually connected Node objects."""

    row: int
    col: int
    value: bool = False
    is_input: bool = False
    connected_nodes: list[Node] = field(default_factory=list, repr=False)
    changed_last_update: bool = False
    outbound_name: str | None = None
    outbound_side: str = "right"


@dataclass(eq=False)
class LogicGate:
    kind: str
    sources: tuple[Node, ...]
    target: Node
    cell: tuple[int, int] | None = None
    rotation: int = 0

    def evaluate(self) -> bool:
        if self.kind in ("AND", "NAND"):
            result = all(node.value for node in self.sources)
            return not result if self.kind == "NAND" else result
        if self.kind in ("OR", "NOR"):
            result = any(node.value for node in self.sources)
            return not result if self.kind == "NOR" else result
        if self.kind == "NOT":
            return not self.sources[0].value
        raise ValueError(f"Unknown gate kind: {self.kind}")


@dataclass(eq=False)
class CircuitComponent:
    label: str
    circuit: GridModel
    row: int
    col: int
    # Each exposed grid node represents the corresponding internal outbound node.
    ports: list[tuple[Node, Node]]

    @property
    def height(self) -> int:
        return max(exposed.row for exposed, _ in self.ports) - self.row

    @staticmethod
    def port_layout(outbound: list[Node], row: int, col: int) -> list[tuple[int, int, Node]]:
        """Stack each side independently, keeping source order on that side."""
        counts = {"left": 0, "right": 0}
        layout = []
        for node in outbound:
            counts[node.outbound_side] += 1
            layout.append((row + counts[node.outbound_side], col + (0 if node.outbound_side == "left" else 2), node))
        return layout

    def contains(self, node: Node) -> bool:
        # Only the middle column lies under the box. Unused side positions
        # remain ordinary grid nodes, even between exposed ports.
        return (self.row < node.row <= self.row + self.height and node.col == self.col + 1) or any(
            (node.row, node.col) == (exposed.row, exposed.col) for exposed, _ in self.ports
        )


class GridModel:
    def __init__(self, rows: int = 8, cols: int = 12) -> None:
        if rows < 1 or cols < 1:
            raise ValueError("The grid must have at least one row and column.")
        self.rows = rows
        self.cols = cols
        # These dimensions describe the initial view, not the world boundary.
        # Keep the original rectangle for compatibility; other coordinates are
        # sparse, so distant edits never allocate the space between them.
        self.nodes = [[Node(row, col) for col in range(cols)] for row in range(rows)]
        self.extra_nodes: dict[tuple[int, int], Node] = {}
        self.update_count = 0
        self.gates: list[LogicGate] = []
        self._original_target_roles: dict[Node, bool] = {}
        self.components: list[CircuitComponent] = []

    def all_nodes(self) -> Iterator[Node]:
        for row in self.nodes:
            yield from row
        for coordinate in sorted(self.extra_nodes):
            yield self.extra_nodes[coordinate]

    def get_node(self, row: int, col: int, *, create: bool = True) -> Node:
        """Get any signed grid coordinate; previews don't allocate empty space."""
        if type(row) is not int or type(col) is not int:
            raise ValueError("Node coordinates must be integers.")
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self.nodes[row][col]
        key = (row, col)
        node = self.extra_nodes.get(key)
        if node is None:
            node = Node(row, col)
            if create:
                self.extra_nodes[key] = node
        return node

    def owns_node(self, node: Node) -> bool:
        return self.get_node(node.row, node.col, create=False) is node

    def configured_nodes(self) -> Iterator[Node]:
        """Nodes whose state or topology is meaningful even outside the view."""
        linked = {node for gate in self.gates for node in (*gate.sources, gate.target)}
        linked.update(exposed for component in self.components for exposed, _ in component.ports)
        for node in self.all_nodes():
            if (node.value or node.is_input or node.changed_last_update
                    or node.outbound_name is not None or node.connected_nodes or node in linked):
                yield node

    def toggle_connection(self, first: Node, second: Node) -> bool:
        """Toggle a grid edge, returning whether it is now active."""
        for node in (first, second):
            if not self.owns_node(node):
                raise ValueError("Both nodes must belong to this grid.")
            self.check_accessible(node)
        if abs(first.row - second.row) + abs(first.col - second.col) != 1:
            raise ValueError("Only horizontal or vertical neighbours can connect.")
        if self.not_gate_on_edge(first, second) is not None:
            raise ValueError("Right-click this NOT gate to turn it into a normal edge.")
        if second in first.connected_nodes:
            first.connected_nodes.remove(second)
            second.connected_nodes.remove(first)
            return False
        first.connected_nodes.append(second)
        second.connected_nodes.append(first)
        return True

    def active_edges(self) -> Iterator[tuple[Node, Node]]:
        """Yield every undirected connection once."""
        for node in self.all_nodes():
            for neighbour in node.connected_nodes:
                if (node.row, node.col) < (neighbour.row, neighbour.col):
                    yield node, neighbour

    def toggle_type(self, node: Node) -> None:
        self.check_accessible(node)
        if node.outbound_name is not None:
            raise ValueError("Remove the outbound name before changing this output node's type.")
        if self.component_port(node) is not None:
            raise ValueError("An imported circuit port is an output node.")
        if self.gate_role(node) is not None:
            raise ValueError("This node is driven by a gate. Remove its driving gate to change its type.")
        node.is_input = not node.is_input

    def toggle_input(self, node: Node) -> bool:
        """Only input nodes can change value directly through a click."""
        self.check_accessible(node)
        if not node.is_input or self.gate_driver(node) is not None:
            return False
        node.value = not node.value
        return True

    def gate_driver(self, node: Node) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.target is node), None)

    def gate_role(self, node: Node) -> bool | None:
        """Only a gate's driven target has a forced input type."""
        if self.gate_driver(node) is not None:
            return True
        return None

    def is_connected(self, node: Node) -> bool:
        return self.component_port(node) is not None or bool(node.connected_nodes) or any(
            node is gate.target or node in gate.sources for gate in self.gates
        )

    def outbound_nodes(self) -> list[Node]:
        return sorted(
            (node for node in self.all_nodes() if node.outbound_name is not None),
            key=lambda node: (node.row, node.col),
        )

    def set_outbound(self, node: Node, name: str | None) -> None:
        if not self.owns_node(node):
            raise ValueError("The outbound node must belong to this circuit.")
        self.check_accessible(node)
        if name is None:
            node.outbound_name = None
            node.outbound_side = "right"
            return
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
            raise ValueError("Use an outbound name containing 1 to 40 characters.")
        name = name.strip()
        if any(not character.isprintable() for character in name):
            raise ValueError("Outbound names must be a single line of printable text.")
        if any(other is not node and other.outbound_name == name for other in self.all_nodes()):
            raise ValueError("Each outbound name must be unique within this circuit.")
        if self.gate_driver(node) is not None:
            raise ValueError("An outbound must be an output node, not a gate-driven IN node.")
        node.is_input = False
        node.outbound_name = name

    def set_outbound_side(self, node: Node, side: str) -> None:
        if not self.owns_node(node) or node.outbound_name is None:
            raise ValueError("Name this circuit's outbound node before choosing its import side.")
        if side not in ("left", "right"):
            raise ValueError("Choose left or right for the outbound's import side.")
        self.check_accessible(node)
        node.outbound_side = side

    def component_port(self, node: Node) -> tuple[CircuitComponent, Node] | None:
        for component in self.components:
            for exposed, internal in component.ports:
                if exposed is node:
                    return component, internal
        return None

    def component_at_node(self, node: Node) -> CircuitComponent | None:
        return next((component for component in self.components if component.contains(node)), None)

    def check_accessible(self, node: Node) -> None:
        if self.component_at_node(node) is not None and self.component_port(node) is None:
            raise ValueError("Connect to the named ports outside the imported circuit's box.")

    def import_circuit(self, circuit: GridModel, row: int, col: int, label: str) -> CircuitComponent:
        # Each import owns its state, including nested imported circuits.
        independent = GridModel.from_dict(circuit.to_dict(), _depth=1)
        return self._place_component(independent, row, col, label)

    def _place_component(
        self, circuit: GridModel, row: int, col: int, label: str, *, restoring: bool = False,
    ) -> CircuitComponent:
        outbound = circuit.outbound_nodes()
        if not outbound:
            raise ValueError("This circuit has no outbound nodes. Name an outbound node and save it first.")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("An imported circuit name must contain 1 to 80 characters.")
        if type(row) is not int or type(col) is not int:
            raise ValueError("Component coordinates must be integers.")
        layout = CircuitComponent.port_layout(outbound, row, col)
        height = max(port_row for port_row, _, _ in layout) - row
        exposed_positions = {(port_row, port_col) for port_row, port_col, _ in layout}
        covered_positions = {(covered_row, col + 1) for covered_row in range(row + 1, row + height + 1)}
        for component in self.components:
            if (abs(col - component.col) < 2
                    and row + 1 <= component.row + component.height
                    and component.row + 1 <= row + height):
                raise ValueError("The imported circuit would overlap an existing component's box.")
        for gate in self.gates:
            if gate.cell is not None:
                gr, gc = gate.cell
                if gr <= row + height and row + 1 <= gr + 1 and gc <= col + 1 <= gc + 1:
                    raise ValueError("The imported circuit would overlap an existing gate.")
        for position in covered_positions | exposed_positions:
            node = self.get_node(*position, create=False)
            if self.component_at_node(node) is not None or self.is_connected(node):
                raise ValueError("The imported circuit would overlap existing wires, gates, or components.")
            if restoring and position in exposed_positions:
                if node.is_input:
                    raise ValueError("Imported circuit ports must be output nodes.")
            elif node.is_input or node.value or node.changed_last_update or node.outbound_name is not None:
                raise ValueError("Choose an empty area for the imported circuit.")
        for port_row, port_col, internal in layout:
            exposed = self.get_node(port_row, port_col, create=False)
            if restoring and (exposed.value, exposed.changed_last_update) != (
                internal.value, internal.changed_last_update,
            ):
                raise ValueError("The saved external port state does not match its internal outbound node.")
        # Materialize ports only after every placement check has succeeded.
        ports = [(self.get_node(port_row, port_col), internal) for port_row, port_col, internal in layout]
        for exposed, internal in ports:
            exposed.value = internal.value
            exposed.changed_last_update = internal.changed_last_update
        component = CircuitComponent(label.strip(), circuit, row, col, ports)
        self.components.append(component)
        return component

    def remove_component(self, component: CircuitComponent) -> None:
        self.components.remove(component)
        # The exposed nodes become ordinary outputs; keep the surrounding wiring.
        for exposed, _ in component.ports:
            exposed.value = False
            exposed.changed_last_update = False

    def gate_in_cell(self, row: int, col: int) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.cell == (row, col)), None)

    def not_gate_on_edge(self, first: Node, second: Node) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.kind == "NOT" and (
            (gate.sources[0] is first and gate.target is second)
            or (gate.sources[0] is second and gate.target is first)
        )), None)

    def add_binary_gate(self, kind: str, row: int, col: int, rotation: int = 0) -> LogicGate:
        if kind not in BINARY_GATE_KINDS:
            raise ValueError("Choose AND, OR, NAND, or NOR for a grid square.")
        if type(row) is not int or type(col) is not int:
            raise ValueError("Gate coordinates must be integers.")
        corners = (
            self.get_node(row, col), self.get_node(row, col + 1),
            self.get_node(row + 1, col + 1), self.get_node(row + 1, col),
        )
        for corner in corners:
            self.check_accessible(corner)
        rotation %= 4
        # Start with the two left corners as sources and upper-right as target.
        gate = LogicGate(
            kind, (corners[rotation], corners[(3 + rotation) % 4]),
            corners[(1 + rotation) % 4], (row, col), rotation,
        )
        return self._install_gate(gate, self.gate_in_cell(row, col))

    def set_not_gate(self, source: Node, target: Node) -> LogicGate:
        for node in (source, target):
            if not self.owns_node(node):
                raise ValueError("Both ends of a NOT gate must belong to this grid.")
        if abs(source.row - target.row) + abs(source.col - target.col) != 1:
            raise ValueError("A NOT gate must occupy a neighbouring grid edge.")
        return self._install_gate(
            LogicGate("NOT", (source,), target), self.not_gate_on_edge(source, target)
        )

    def _install_gate(self, gate: LogicGate, replaces: LogicGate | None) -> LogicGate:
        # Validate first so an impossible placement/rotation leaves the circuit intact.
        for node in (*gate.sources, gate.target):
            self.check_accessible(node)
        if gate.target.outbound_name is not None or self.component_port(gate.target) is not None:
            raise ValueError("A gate cannot drive an outbound output or an imported circuit port.")
        for other in self.gates:
            if other is replaces:
                continue
            if other.target is gate.target:
                raise ValueError("A node can be driven by only one gate.")
        if replaces is not None:
            self.remove_gate(replaces, restore_wire=False)
        # Sources keep their current types, including manual inputs and nodes
        # driven by other gates. Only this gate's target needs a forced type.
        self._original_target_roles[gate.target] = gate.target.is_input
        gate.target.is_input = True
        if gate.kind == "NOT":
            source = gate.sources[0]
            if gate.target in source.connected_nodes:
                source.connected_nodes.remove(gate.target)
                gate.target.connected_nodes.remove(source)
        self.gates.append(gate)
        return gate

    def rotate_gate(self, gate: LogicGate) -> LogicGate:
        if gate not in self.gates:
            raise ValueError("This gate is no longer on the grid.")
        if gate.cell is not None:
            return self.add_binary_gate(gate.kind, *gate.cell, gate.rotation + 1)
        return self.set_not_gate(gate.target, gate.sources[0])

    def remove_gate(self, gate: LogicGate, restore_wire: bool = True) -> None:
        self.gates.remove(gate)
        gate.target.is_input = self._original_target_roles.pop(gate.target)
        if gate.kind == "NOT" and restore_wire:
            source = gate.sources[0]
            if gate.target not in source.connected_nodes:
                source.connected_nodes.append(gate.target)
                gate.target.connected_nodes.append(source)

    def to_dict(self) -> dict:
        """Store node coordinates instead of Python object references."""
        def node_state(node: Node) -> dict:
            return {
                "value": int(node.value),
                "base_is_input": self._original_target_roles.get(node, node.is_input),
                "changed_last_update": node.changed_last_update,
                "outbound_name": node.outbound_name,
                "outbound_side": node.outbound_side,
            }

        gates = []
        for gate in self.gates:
            if gate.cell is not None:
                gates.append({"kind": gate.kind, "cell": list(gate.cell), "rotation": gate.rotation})
            else:
                gates.append({
                    "kind": "NOT",
                    "source": [gate.sources[0].row, gate.sources[0].col],
                    "target": [gate.target.row, gate.target.col],
                })
        return {
            "rows": self.rows,
            "cols": self.cols,
            "update_count": self.update_count,
            "nodes": [[node_state(node) for node in row] for row in self.nodes],
            "extra_nodes": [
                {"row": node.row, "col": node.col, **node_state(node)}
                for node in self.configured_nodes() if (node.row, node.col) in self.extra_nodes
            ],
            "edges": [
                [[first.row, first.col], [second.row, second.col]]
                for first, second in self.active_edges()
            ],
            "gates": gates,
            "components": [{
                "label": component.label, "row": component.row, "col": component.col,
                "circuit": component.circuit.to_dict(),
            } for component in self.components],
        }

    @classmethod
    def from_dict(cls, data: object, *, _depth: int = 0) -> GridModel:
        """Validate and construct a separate model without changing a live grid."""
        if _depth > 8:
            raise ValueError("Imported circuits can be nested up to eight levels deep.")
        def integer(value: object, name: str, minimum: int | None = 0) -> int:
            if type(value) is not int or (minimum is not None and value < minimum):
                limit = f" of at least {minimum}" if minimum is not None else ""
                raise ValueError(f"{name} must be an integer{limit}.")
            return value

        def boolean(value: object, name: str) -> bool:
            if type(value) is not bool:
                raise ValueError(f"{name} must be true or false.")
            return value

        def coordinate(value: object) -> tuple[int, int]:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("A node coordinate must contain a row and column.")
            row = integer(value[0], "Node row", None if infinite else 0)
            col = integer(value[1], "Node column", None if infinite else 0)
            if not infinite and (row >= rows or col >= cols):
                raise ValueError("A node coordinate is outside the saved grid.")
            return row, col

        if not isinstance(data, dict):
            raise ValueError("The saved grid must be a JSON object.")
        rows = integer(data.get("rows"), "Rows", 1)
        cols = integer(data.get("cols"), "Columns", 1)
        infinite = "extra_nodes" in data
        update_count = integer(data.get("update_count"), "Update count")
        nodes = data.get("nodes")
        if (
            not isinstance(nodes, list) or len(nodes) != rows
            or any(not isinstance(row, list) or len(row) != cols for row in nodes)
        ):
            raise ValueError("The saved node array does not match the grid dimensions.")
        edges, gates = data.get("edges"), data.get("gates")
        components = data.get("components", [])
        extra_nodes = data.get("extra_nodes", [])
        if not all(isinstance(items, list) for items in (edges, gates, components, extra_nodes)):
            raise ValueError("The saved edges, gates, components, and extra nodes must be lists.")

        grid = cls(rows, cols)
        grid.update_count = update_count
        def restore_node(node: Node, saved: object) -> None:
            if not isinstance(saved, dict):
                raise ValueError("Each saved node must be a JSON object.")
            value = saved.get("value")
            if type(value) is not int or value not in (0, 1):
                raise ValueError("A saved node value must be 0 or 1.")
            node.value = bool(value)
            node.is_input = boolean(saved.get("base_is_input"), "Node type")
            node.changed_last_update = boolean(saved.get("changed_last_update"), "Cooldown")
            name = saved.get("outbound_name")
            side = saved.get("outbound_side", "right")
            if side not in ("left", "right"):
                raise ValueError("An outbound's import side must be left or right.")
            if name is None and side != "right":
                raise ValueError("Only named outbound nodes can select an import side.")
            if name is not None:
                if node.is_input:
                    raise ValueError("Outbound nodes must be output nodes.")
                grid.set_outbound(node, name)
                grid.set_outbound_side(node, side)

        for row, saved_row in zip(grid.nodes, nodes):
            for node, saved in zip(row, saved_row):
                restore_node(node, saved)
        for saved in extra_nodes:
            if not isinstance(saved, dict):
                raise ValueError("Each extra node must be a JSON object.")
            row, col = coordinate([saved.get("row"), saved.get("col")])
            if (0 <= row < rows and 0 <= col < cols) or (row, col) in grid.extra_nodes:
                raise ValueError("The file contains a duplicate node coordinate.")
            restore_node(grid.get_node(row, col), saved)

        # Reserve component bodies and bind their ports before rebuilding wires/gates.
        for saved in components:
            if not isinstance(saved, dict):
                raise ValueError("Each imported circuit must be a JSON object.")
            row, col = coordinate([saved.get("row"), saved.get("col")])
            circuit = cls.from_dict(saved.get("circuit"), _depth=_depth + 1)
            if not infinite and (row + len(circuit.outbound_nodes()) + 1 > rows or col + 3 > cols):
                raise ValueError("The component is outside the saved grid.")
            grid._place_component(circuit, row, col, saved.get("label"), restoring=True)

        for edge in edges:
            if not isinstance(edge, list) or len(edge) != 2:
                raise ValueError("Each saved edge must contain two node coordinates.")
            first_pos, second_pos = coordinate(edge[0]), coordinate(edge[1])
            first = grid.get_node(*first_pos)
            second = grid.get_node(*second_pos)
            if second in first.connected_nodes:
                raise ValueError("The file contains a duplicate edge.")
            grid.toggle_connection(first, second)

        for gate in gates:
            if not isinstance(gate, dict):
                raise ValueError("Each saved gate must be a JSON object.")
            kind = gate.get("kind")
            if kind in BINARY_GATE_KINDS:
                row, col = coordinate(gate.get("cell"))
                if not infinite and (row + 1 >= rows or col + 1 >= cols):
                    raise ValueError("The gate is outside the saved grid.")
                rotation = integer(gate.get("rotation"), "Gate rotation")
                if rotation > 3:
                    raise ValueError("Gate rotation must be between 0 and 3.")
                if grid.gate_in_cell(row, col) is not None:
                    raise ValueError("The file contains two gates in the same square.")
                grid.add_binary_gate(kind, row, col, rotation)
            elif kind == "NOT":
                sr, sc = coordinate(gate.get("source"))
                tr, tc = coordinate(gate.get("target"))
                source, target = grid.get_node(sr, sc), grid.get_node(tr, tc)
                if grid.not_gate_on_edge(source, target) is not None:
                    raise ValueError("The file contains duplicate NOT gates on one edge.")
                if target in source.connected_nodes:
                    raise ValueError("An edge cannot be both a normal wire and a NOT gate.")
                grid.set_not_gate(source, target)
            else:
                raise ValueError("Unknown gate kind; expected AND, OR, NAND, NOR, or NOT.")
        return grid

    def _simulation_graph(self) -> tuple[list[GridModel], dict[Node, Node], dict[Node, set[Node]]]:
        """Join exposed ports to their actual outbound nodes across all nested circuits."""
        models: list[GridModel] = []
        bindings: dict[Node, Node] = {}

        def collect(model: GridModel) -> None:
            models.append(model)
            for component in model.components:
                bindings.update(component.ports)
                collect(component.circuit)

        collect(self)
        canonical = {}
        for model in models:
            for node in model.all_nodes():
                representative = node
                while representative in bindings:
                    representative = bindings[representative]
                canonical[node] = representative
        neighbours: dict[Node, set[Node]] = {node: set() for node in canonical.values()}
        for node, representative in canonical.items():
            for neighbour in node.connected_nodes:
                other = canonical[neighbour]
                if other is not representative:
                    neighbours[representative].add(other)
        return models, canonical, neighbours

    @staticmethod
    def _sync_ports(canonical: dict[Node, Node]) -> None:
        for exposed, internal in canonical.items():
            if exposed is not internal:
                exposed.value = internal.value
                exposed.changed_last_update = internal.changed_last_update

    def reset_state(self) -> int:
        """Clear connected outputs, including the independent circuits inside components."""
        models, canonical, neighbours = self._simulation_graph()
        connected = {node for node, adjacent in neighbours.items() if adjacent}
        connected.update(internal for exposed, internal in canonical.items() if exposed is not internal)
        for model in models:
            for gate in model.gates:
                connected.update(canonical[node] for node in (*gate.sources, gate.target))
        reset_count = 0
        for node in connected:
            if not node.is_input:
                node.value = False
                node.changed_last_update = False
                reset_count += 1
        self._sync_ports(canonical)
        return reset_count

    def update(self) -> int:
        """Advance eligible outputs simultaneously; return the number changed.

        next value = clamp(value + sign(mean(neighbour values) - value), 0, 1)

        Use pre-update values throughout. Outputs that changed last update hold
        for this update only. Gates drive their input-type targets every update;
        manual inputs and disconnected nodes retain their values.
        """
        models, canonical, neighbours = self._simulation_graph()
        self._sync_ports(canonical)
        pending: list[tuple[Node, bool]] = []
        for node, adjacent in neighbours.items():
            if node.is_input or not adjacent or node.changed_last_update:
                continue
            # Multiplication by the positive neighbour count preserves the
            # sign of the mean difference without floating-point arithmetic.
            difference = sum(int(n.value) for n in adjacent) - (
                len(adjacent) * int(node.value)
            )
            value_change = (difference > 0) - (difference < 0)
            new_value = bool(max(0, min(1, int(node.value) + value_change)))
            if new_value != node.value:
                pending.append((node, new_value))

        # Gates also read the old state; their driven inputs have no cooldown.
        gate_values = [(gate.target, gate.evaluate()) for model in models for gate in model.gates]
        gate_changes = sum(node.value != value for node, value in gate_values)
        # Expire every previous cooldown, even for disconnected nodes and inputs.
        for node in neighbours:
            node.changed_last_update = False
        # Commit together so held nodes still contribute their current values.
        for node, new_value in pending:
            node.value = new_value
            node.changed_last_update = True
        for node, new_value in gate_values:
            node.value = new_value
        self._sync_ports(canonical)
        for model in models:
            model.update_count += 1
        return len(pending) + gate_changes


def parse_update_rate(value: object) -> float:
    try:
        if type(value) not in (str, int, float):
            raise ValueError
        rate = float(value)
        if not math.isfinite(rate) or not 0.01 <= rate <= 1000:
            raise ValueError
        return rate
    except (ValueError, OverflowError):
        raise ValueError("Enter a rate from 0.01 to 1000 updates per second.") from None


def save_setup_file(path: str | Path, model: GridModel, auto_rate: object) -> None:
    """Write atomically so a failed save cannot truncate an existing setup."""
    payload = {
        "format": "boolean-circuit-grid",
        "version": 4,
        "auto_rate": parse_update_rate(auto_rate),
        "grid": model.to_dict(),
    }
    content = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    destination = Path(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_setup_file(path: str | Path) -> tuple[GridModel, float]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict) or payload.get("format") != "boolean-circuit-grid"
        or type(payload.get("version")) is not int or payload["version"] not in (1, 2, 3, 4)
    ):
        raise ValueError("Not a supported Boolean Circuit Grid setup (version 1, 2, 3, or 4).")
    rate = parse_update_rate(payload.get("auto_rate"))
    return GridModel.from_dict(payload.get("grid")), rate


class CircuitGridApp:
    SPACING = 64
    MARGIN = 48
    NODE_RADIUS = 12
    EDGE_HIT_DISTANCE = 8
    BACKGROUND = "#fafafa"
    GRID_COLOR = "#d8dce2"
    ZERO_COLOR = "#d52b32"
    ONE_COLOR = "#188a46"
    DISCONNECTED_COLOR = "#000000"

    def __init__(self, root: tk.Tk, model: GridModel) -> None:
        self.root = root
        self.model = model
        self.auto_running = False
        self.auto_after_id: str | None = None
        self.auto_rate = tk.StringVar(root, value="2")
        self.auto_status = tk.StringVar(root, value="Stopped")
        self.context_menu: tk.Menu | None = None
        self.last_setup_path: Path | None = None
        root.title("Boolean Circuit Grid")
        root.minsize(620, 380)
        root.protocol("WM_DELETE_WINDOW", self.close)

        container = ttk.Frame(root, padding=16)
        container.pack(fill="both", expand=True)
        toolbar = ttk.Frame(container)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Boolean Circuit Grid", font=("Arial", 18, "bold")).pack(
            side="left"
        )
        ttk.Button(toolbar, text="update", command=self.update).pack(side="right")
        ttk.Button(toolbar, text="reset state", command=self.reset_state).pack(
            side="right", padx=(0, 8)
        )

        auto_controls = ttk.Frame(container)
        auto_controls.pack(fill="x", pady=(0, 10))
        self.auto_button = ttk.Button(
            auto_controls, text="auto update", command=self.toggle_auto_update
        )
        self.auto_button.pack(side="left")
        ttk.Entry(auto_controls, textvariable=self.auto_rate, width=8).pack(
            side="left", padx=(8, 5)
        )
        ttk.Label(auto_controls, text="updates / second").pack(side="left")
        ttk.Label(auto_controls, textvariable=self.auto_status).pack(side="left", padx=(12, 0))
        self.auto_rate.trace_add("write", self.on_auto_rate_change)

        file_controls = ttk.Frame(container)
        file_controls.pack(fill="x", pady=(0, 8))
        ttk.Button(file_controls, text="save", command=self.save_setup).pack(side="left")
        ttk.Button(file_controls, text="load", command=self.load_setup).pack(side="left", padx=(8, 0))
        ttk.Button(file_controls, text="go to circuit", command=self.go_to_circuit).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(
            container,
            text="Left-click an edge: toggle connection  •  Left-click a manual input: flip 0 / 1",
        ).pack(anchor="w")
        ttk.Label(
            container,
            text="Right-click: node type / outbound name  •  empty space: gates / import  •  edge: NOT",
        ).pack(anchor="w", pady=(2, 2))
        ttk.Label(
            container,
            text="Left-click a gate: rotate  •  Scroll to move  •  Shift-scroll to move horizontally",
        ).pack(anchor="w", pady=(0, 10))

        grid_frame = ttk.Frame(container)
        grid_frame.pack(fill="both", expand=True)
        grid_frame.rowconfigure(0, weight=1)
        grid_frame.columnconfigure(0, weight=1)
        self.grid_width = 2 * self.MARGIN + (model.cols - 1) * self.SPACING
        self.grid_height = 2 * self.MARGIN + (model.rows - 1) * self.SPACING
        self.canvas = tk.Canvas(
            grid_frame,
            width=min(self.grid_width, 1000),
            height=min(self.grid_height, 640),
            background=self.BACKGROUND,
            highlightthickness=1,
            highlightbackground=self.GRID_COLOR,
            confine=False,
            xscrollincrement=1,
            yscrollincrement=1,
            scrollregion=(0, 0, self.grid_width, self.grid_height),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        # An unconfined canvas can pan past either end of its reference region.
        # Only the visible part of the infinite grid is drawn.
        self.windowing_system = root.tk.call("tk", "windowingsystem")
        self.canvas.bind("<MouseWheel>", self.on_scroll)
        self.canvas.bind("<Shift-MouseWheel>", lambda event: self.on_scroll(event, horizontal=True))
        for button in (4, 5):
            self.canvas.bind(f"<Button-{button}>", self.on_scroll)
            self.canvas.bind(
                f"<Shift-Button-{button}>", lambda event: self.on_scroll(event, horizontal=True)
            )
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Button-1>", self.on_left_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        # macOS Tk can report a secondary click as button 2. Control-click is
        # also supported for users without a secondary mouse/trackpad button.
        if self.windowing_system == "aqua":
            self.canvas.bind("<Button-2>", self.on_right_click)
            self.canvas.bind("<Control-Button-1>", self.on_right_click)

        legend = ttk.Frame(container)
        legend.pack(fill="x", pady=(10, 4))
        for text, color in (
            ("disconnected", self.DISCONNECTED_COLOR),
            ("0", self.ZERO_COLOR),
            ("1", self.ONE_COLOR),
        ):
            swatch = tk.Canvas(
                legend, width=16, height=16, background=self.BACKGROUND, highlightthickness=0
            )
            swatch.create_oval(3, 3, 13, 13, fill=color, outline=color)
            swatch.pack(side="left", padx=(0, 5))
            ttk.Label(legend, text=text).pack(side="left", padx=(0, 20))
        ttk.Label(
            container,
            text=(
                "Connected outputs add sign(neighbour average - value), using pre-update values. "
                "An output that changes holds its value for the next update. "
                "Gates read the same pre-update values and drive their IN nodes every update."
            ),
            wraplength=760,
        ).pack(anchor="w")
        self.status = tk.StringVar()
        ttk.Label(container, textvariable=self.status, wraplength=760).pack(anchor="w", pady=(8, 0))
        self.message = "Activate an edge or right-click a node to begin."
        self.draw()

    def on_scroll(self, event: tk.Event, horizontal: bool = False) -> str:
        """Mouse wheels and trackpads pan without a finite scroll boundary."""
        button = getattr(event, "num", None)
        if button in (4, 5):
            pixels = -40 if button == 4 else 40
        else:
            delta = getattr(event, "delta", 0)
            scale = 8 if self.windowing_system == "aqua" else 40 / 120
            pixels = round(-delta * scale)
            if delta and not pixels:
                pixels = -1 if delta > 0 else 1
        if pixels:
            horizontal = horizontal or bool(getattr(event, "state", 0) & 0x1)
            scroll = self.canvas.xview_scroll if horizontal else self.canvas.yview_scroll
            scroll(pixels, "units")
            self.draw()
        return "break"

    def go_to_circuit(self) -> None:
        """Return to the top-left occupied area, or the origin for an empty grid."""
        positions = [(node.row, node.col) for node in self.model.configured_nodes()]
        positions.extend((component.row, component.col) for component in self.model.components)
        row = min((row for row, _ in positions), default=0)
        col = min((col for _, col in positions), default=0)
        self.canvas.xview_moveto(col * self.SPACING / self.grid_width)
        self.canvas.yview_moveto(row * self.SPACING / self.grid_height)
        self.draw()

    def viewport_bounds(self) -> tuple[float, float, float, float]:
        return (
            self.canvas.canvasx(0), self.canvas.canvasy(0),
            self.canvas.canvasx(self.canvas.winfo_width()),
            self.canvas.canvasy(self.canvas.winfo_height()),
        )

    def position(self, node: Node) -> tuple[int, int]:
        return self.MARGIN + node.col * self.SPACING, self.MARGIN + node.row * self.SPACING

    def component_bounds(self, component: CircuitComponent) -> tuple[int, int, int, int]:
        x = self.MARGIN + component.col * self.SPACING
        first_y = self.MARGIN + (component.row + 1) * self.SPACING
        last_y = self.MARGIN + (component.row + component.height) * self.SPACING
        return x + 18, first_y - 24, x + 2 * self.SPACING - 18, last_y + 24

    def component_at(self, x: float, y: float) -> CircuitComponent | None:
        for component in reversed(self.model.components):
            left, top, right, bottom = self.component_bounds(component)
            if left <= x <= right and top <= y <= bottom:
                return component
        return None

    def draw_component(self, component: CircuitComponent) -> None:
        left, top, right, bottom = self.component_bounds(component)
        self.canvas.create_rectangle(
            left, top, right, bottom, fill="#eaf4fb", outline="#276a91", width=2,
        )
        self.canvas.create_text(
            (left + right) / 2, top + 3,
            text=component.label[:12] + ("…" if len(component.label) > 12 else ""), anchor="n",
            font=("Arial", 8, "bold"), fill="#174c70",
        )
        for exposed, internal in component.ports:
            x, y = self.position(exposed)
            on_left = exposed.col == component.col
            edge_x = left if on_left else right
            self.canvas.create_line(edge_x, y, x, y, fill=self.value_color(exposed), width=3)
            name = internal.outbound_name or ""
            short_name = name if len(name) <= 5 else name[:4] + "…"
            self.canvas.create_text(
                left + 4 if on_left else right - 4, y, text=short_name,
                anchor="w" if on_left else "e", font=("Arial", 8), fill="#174c70",
            )

    def gate_center(self, gate: LogicGate) -> tuple[float, float]:
        if gate.cell is not None:
            row, col = gate.cell
            return (
                self.MARGIN + (col + 0.5) * self.SPACING,
                self.MARGIN + (row + 0.5) * self.SPACING,
            )
        sx, sy = self.position(gate.sources[0])
        tx, ty = self.position(gate.target)
        return (sx + tx) / 2, (sy + ty) / 2

    def gate_bounds(self, gate: LogicGate) -> tuple[float, float, float, float]:
        x, y = self.gate_center(gate)
        half_width, half_height = (14, 10) if gate.kind == "NOT" else (20, 12)
        return x - half_width, y - half_height, x + half_width, y + half_height

    def gate_at(self, x: float, y: float) -> LogicGate | None:
        for gate in reversed(self.model.gates):
            left, top, right, bottom = self.gate_bounds(gate)
            if left <= x <= right and top <= y <= bottom:
                return gate
        return None

    def cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        row = math.floor((y - self.MARGIN) / self.SPACING)
        col = math.floor((x - self.MARGIN) / self.SPACING)
        return row, col

    def draw_gate(self, gate: LogicGate) -> None:
        cx, cy = self.gate_center(gate)
        left, top, right, bottom = self.gate_bounds(gate)
        for node in (*gate.sources, gate.target):
            nx, ny = self.position(node)
            dx, dy = nx - cx, ny - cy
            scale = 1 / max(abs(dx) / (right - cx), abs(dy) / (bottom - cy))
            port = (cx + dx * scale, cy + dy * scale)
            if node is gate.target:
                distance = math.hypot(dx, dy)
                endpoint = (
                    nx - dx / distance * (self.NODE_RADIUS + 2),
                    ny - dy / distance * (self.NODE_RADIUS + 2),
                )
                self.canvas.create_line(
                    *port, *endpoint, fill=self.value_color(node), width=3,
                    arrow="last", arrowshape=(5, 6, 3),
                )
            else:
                self.canvas.create_line(
                    nx, ny, *port, fill=self.value_color(node), width=3,
                    arrow="last", arrowshape=(4, 5, 2),
                )
        self.canvas.create_rectangle(
            left, top, right, bottom, fill="#f0ebff", outline="#61439b", width=2,
        )
        self.canvas.create_text(
            cx, cy, text=gate.kind, fill="#422a73", font=("Arial", 8, "bold"),
        )

    def value_color(self, node: Node) -> str:
        return self.ONE_COLOR if node.value else self.ZERO_COLOR

    def draw(self) -> None:
        self.canvas.delete("all")
        left, top, right, bottom = self.viewport_bounds()
        first_row = math.floor((top - self.MARGIN) / self.SPACING) - 1
        last_row = math.ceil((bottom - self.MARGIN) / self.SPACING) + 1
        first_col = math.floor((left - self.MARGIN) / self.SPACING) - 1
        last_col = math.ceil((right - self.MARGIN) / self.SPACING) + 1

        def visible(x1: float, y1: float, x2: float, y2: float) -> bool:
            padding = self.SPACING  # Include labels, gate links, and port stubs.
            return (
                max(x1, x2) >= left - padding and min(x1, x2) <= right + padding
                and max(y1, y2) >= top - padding and min(y1, y2) <= bottom + padding
            )

        for row in range(first_row, last_row + 1):
            y = self.MARGIN + row * self.SPACING
            self.canvas.create_line(
                left, y, right, y, fill=self.GRID_COLOR, width=1
            )
        for col in range(first_col, last_col + 1):
            x = self.MARGIN + col * self.SPACING
            self.canvas.create_line(
                x, top, x, bottom, fill=self.GRID_COLOR, width=1
            )

        edges = list(self.model.active_edges())
        for first, second in edges:
            x1, y1 = self.position(first)
            x2, y2 = self.position(second)
            if not visible(x1, y1, x2, y2):
                continue
            midpoint = ((x1 + x2) / 2, (y1 + y2) / 2)
            self.canvas.create_line(
                x1, y1, *midpoint, fill=self.value_color(first), width=5
            )
            self.canvas.create_line(
                *midpoint, x2, y2, fill=self.value_color(second), width=5
            )

        for gate in self.model.gates:
            if visible(*self.gate_bounds(gate)):
                self.draw_gate(gate)

        for component in self.model.components:
            if visible(*self.component_bounds(component)):
                self.draw_component(component)

        visible_nodes = (
            self.model.get_node(row, col, create=False)
            for row in range(first_row, last_row + 1)
            for col in range(first_col, last_col + 1)
        )
        for node in visible_nodes:
            port = self.model.component_port(node)
            if self.model.component_at_node(node) is not None and port is None:
                continue
            x, y = self.position(node)
            color = self.value_color(node) if self.model.is_connected(node) else self.DISCONNECTED_COLOR
            radius = self.NODE_RADIUS
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=color, outline="#216eac" if node.outbound_name or port is not None else color,
                width=2 if node.outbound_name or port is not None else 1,
            )
            self.canvas.create_text(
                x, y, text=str(int(node.value)), fill="white", font=("Arial", 11, "bold")
            )
            if node.is_input:
                self.canvas.create_text(
                    x + radius + 3, y - radius - 3,
                    text="IN", anchor="sw", fill="#252a34", font=("Arial", 9, "bold"),
                )
            if node.outbound_name is not None:
                short_name = node.outbound_name[:12] + ("…" if len(node.outbound_name) > 12 else "")
                self.canvas.create_text(
                    x, y + radius + 5,
                    text=f"OUT ← {short_name}" if node.outbound_side == "left" else f"OUT {short_name} →", anchor="n",
                    width=self.SPACING - 4, fill="#174c70", font=("Arial", 8, "bold"),
                )

        inputs = sum(node.is_input for node in self.model.all_nodes())
        self.status.set(
            f"Updates: {self.model.update_count}   |   Active edges: {len(edges)}   |   "
            f"Inputs: {inputs}   |   Gates: {len(self.model.gates)}   |   "
            f"Circuits: {len(self.model.components)}   |   {self.message}"
        )

    def event_position(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def node_at(self, x: float, y: float) -> Node | None:
        col = round((x - self.MARGIN) / self.SPACING)
        row = round((y - self.MARGIN) / self.SPACING)
        nx, ny = self.MARGIN + col * self.SPACING, self.MARGIN + row * self.SPACING
        if math.hypot(x - nx, y - ny) <= self.NODE_RADIUS + 2:
            node = self.model.get_node(row, col, create=False)
            if self.model.component_at_node(node) is None or self.model.component_port(node) is not None:
                return self.model.get_node(row, col)
        return None

    def edge_at(self, x: float, y: float) -> tuple[Node, Node] | None:
        """Find a neighbouring edge near the click, excluding the node circles."""
        if self.node_at(x, y) is not None:
            return None
        grid_x = (x - self.MARGIN) / self.SPACING
        grid_y = (y - self.MARGIN) / self.SPACING

        def accessible_edge(r1: int, c1: int, r2: int, c2: int) -> tuple[Node, Node] | None:
            try:
                for row, col in ((r1, c1), (r2, c2)):
                    self.model.check_accessible(self.model.get_node(row, col, create=False))
            except ValueError:
                return None
            return self.model.get_node(r1, c1), self.model.get_node(r2, c2)

        row, col = round(grid_y), math.floor(grid_x)
        if abs(grid_y - row) * self.SPACING <= self.EDGE_HIT_DISTANCE:
            return accessible_edge(row, col, row, col + 1)

        row, col = math.floor(grid_y), round(grid_x)
        if abs(grid_x - col) * self.SPACING <= self.EDGE_HIT_DISTANCE:
            return accessible_edge(row, col, row + 1, col)
        return None

    def on_left_click(self, event: tk.Event) -> None:
        x, y = self.event_position(event)
        if self.component_at(x, y) is not None:
            return
        gate = self.gate_at(x, y)
        if gate is not None:
            self.edit_gate(lambda: self.model.rotate_gate(gate), f"{gate.kind} gate rotated.")
            return
        node = self.node_at(x, y)
        if node is not None:
            if self.model.toggle_input(node):
                self.message = f"Input ({node.row + 1}, {node.col + 1}) = {int(node.value)}."
                self.draw()
            elif self.model.gate_driver(node) is not None:
                self.message = "This IN node is driven by its gate on every update."
                self.draw()
            return
        edge = self.edge_at(x, y)
        if edge is not None:
            try:
                active = self.model.toggle_connection(*edge)
                self.message = "Connection activated." if active else "Connection removed."
            except ValueError as error:
                self.message = str(error)
            self.draw()

    def on_right_click(self, event: tk.Event) -> str:
        x, y = self.event_position(event)
        if self.context_menu is not None:
            self.context_menu.destroy()
        menu = self.context_menu = tk.Menu(self.root, tearoff=False)
        component = self.component_at(x, y)
        if component is not None:
            menu.add_command(label=component.label, state="disabled")
            menu.add_command(
                label="Remove imported circuit",
                command=lambda: self.edit_gate(
                    lambda: self.model.remove_component(component),
                    "Imported circuit removed; external wiring is preserved.",
                ),
            )
            return self.popup_menu(menu, event)
        node = self.node_at(x, y)
        if node is not None:
            port = self.model.component_port(node)
            driver = self.model.gate_driver(node)
            if port is not None:
                menu.add_command(label=f"Imported port: {port[1].outbound_name}", state="disabled")
            if driver is None and port is None and node.outbound_name is None:
                kind = "output" if node.is_input else "input"
                menu.add_command(
                    label=f"Switch to {kind}",
                    command=lambda: self.edit_gate(lambda: self.model.toggle_type(node), f"Node is now an {kind}."),
                )
            if driver is None:
                menu.add_command(
                    label="Rename outbound..." if node.outbound_name is not None else "Set outbound name...",
                    command=lambda: self.name_outbound(node),
                )
                if node.outbound_name is not None:
                    for side in ("left", "right"):
                        menu.add_command(
                            label=f"Show on {side} when imported",
                            state="disabled" if node.outbound_side == side else "normal",
                            command=lambda side=side: self.edit_gate(
                                lambda: self.model.set_outbound_side(node, side),
                                f"Outbound will appear on the {side} when this circuit is imported.",
                            ),
                        )
                    menu.add_command(
                        label="Remove outbound name",
                        command=lambda: self.edit_gate(
                            lambda: self.model.set_outbound(node, None), "Outbound designation removed."
                        ),
                    )
            else:
                menu.add_command(label="Gate-driven IN node", state="disabled")
            return self.popup_menu(menu, event)

        gate = self.gate_at(x, y)
        if gate is not None and gate.kind == "NOT":
            edge = tuple(sorted((*gate.sources, gate.target), key=lambda n: (n.row, n.col)))
        else:
            edge = self.edge_at(x, y) if gate is None else None
        if edge is not None:
            first, second = edge
            gate = self.model.not_gate_on_edge(first, second)
            forward, backward = (
                ("left → right", "right → left") if first.row == second.row
                else ("top → bottom", "bottom → top")
            )
            menu.add_command(
                label=f"NOT: {forward}",
                command=lambda: self.edit_gate(
                    lambda: self.model.set_not_gate(first, second), "NOT gate placed."
                ),
            )
            menu.add_command(
                label=f"NOT: {backward}",
                command=lambda: self.edit_gate(
                    lambda: self.model.set_not_gate(second, first), "NOT gate placed."
                ),
            )
            if gate is not None:
                menu.add_separator()
                menu.add_command(
                    label="Make normal edge (remove NOT)",
                    command=lambda: self.edit_gate(
                        lambda: self.model.remove_gate(gate), "NOT replaced with a normal edge."
                    ),
                )
        else:
            cell = gate.cell if gate is not None else self.cell_at(x, y)
            if cell is not None:
                gate = self.model.gate_in_cell(*cell)
                rotation = gate.rotation if gate is not None else 0
                for kind in BINARY_GATE_KINDS:
                    menu.add_command(
                        label=f"Place {kind} gate" if gate is None else f"Use {kind} gate",
                        command=lambda kind=kind: self.edit_gate(
                            lambda: self.model.add_binary_gate(kind, *cell, rotation),
                            f"{kind} gate placed. Left-click it to rotate.",
                        ),
                    )
            if gate is not None:
                menu.add_separator()
                menu.add_command(
                    label="Rotate clockwise",
                    command=lambda: self.edit_gate(
                        lambda: self.model.rotate_gate(gate), f"{gate.kind} gate rotated."
                    ),
                )
                menu.add_command(
                    label="Remove gate",
                    command=lambda: self.edit_gate(
                        lambda: self.model.remove_gate(gate), "Gate removed."
                    ),
                )
            else:
                if cell is not None:
                    menu.add_separator()
                menu.add_command(label="Import circuit...", command=lambda: self.import_circuit_at(x, y))
        return self.popup_menu(menu, event)

    def popup_menu(self, menu: tk.Menu, event: tk.Event) -> str:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def name_outbound(self, node: Node) -> None:
        self.cancel_auto_update()
        try:
            name = simpledialog.askstring(
                "Outbound node", "Name for this outbound output node:",
                initialvalue=node.outbound_name or "", parent=self.root,
            )
            if name is None:
                return
            self.model.set_outbound(node, name)
            self.message = f"Outbound output named {node.outbound_name}."
            self.draw()
        except ValueError as error:
            messagebox.showerror("Could not name outbound", str(error), parent=self.root)
        finally:
            if self.auto_running:
                self.schedule_auto_update()

    def import_circuit_at(self, x: float, y: float) -> None:
        self.cancel_auto_update()
        try:
            filename = filedialog.askopenfilename(
                parent=self.root, title="Import circuit", initialdir=self.setup_directory(),
                filetypes=[("Circuit setup", "*.json"), ("All files", "*")],
            )
            if not filename:
                return
            circuit, _ = load_setup_file(filename)
            row = math.floor((y - self.MARGIN) / self.SPACING)
            col = math.floor((x - self.MARGIN) / self.SPACING)
            label = Path(filename).stem[:80]
            component = self.model.import_circuit(circuit, row, col, label)
            self.message = f"Imported {label}: connect wires to its {len(component.ports)} named side ports."
            self.draw()
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not import circuit", str(error), parent=self.root)
        finally:
            if self.auto_running:
                self.schedule_auto_update()

    def edit_gate(self, action: Callable[[], object], message: str) -> None:
        try:
            action()
            self.message = message
        except ValueError as error:
            self.message = str(error)
        self.draw()

    def update(self) -> None:
        changed = self.model.update()
        self.message = f"{changed} node value{'s' if changed != 1 else ''} changed."
        self.draw()

    def reset_state(self) -> None:
        count = self.model.reset_state()
        self.message = f"Reset {count} output{'s' if count != 1 else ''} to 0; cooldowns cleared."
        self.draw()

    def setup_directory(self) -> str:
        return str(
            self.last_setup_path.parent if self.last_setup_path is not None
            else Path(__file__).resolve().parent
        )

    def save_setup(self) -> None:
        # Native dialogs run their own event loop; suspend ticks until they close.
        self.cancel_auto_update()
        try:
            filename = filedialog.asksaveasfilename(
                parent=self.root, title="Save circuit setup",
                initialdir=self.setup_directory(),
                initialfile=self.last_setup_path.name if self.last_setup_path else "circuit_setup.json",
                defaultextension=".json", filetypes=[("Circuit setup", "*.json")],
            )
            if not filename:
                return
            save_setup_file(filename, self.model, self.auto_rate.get())
            self.last_setup_path = Path(filename)
            self.message = f"Saved {self.last_setup_path.name}."
            self.draw()
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save setup", str(error), parent=self.root)
        finally:
            if self.auto_running:
                self.schedule_auto_update()

    def load_setup(self) -> None:
        self.cancel_auto_update()
        try:
            filename = filedialog.askopenfilename(
                parent=self.root, title="Load circuit setup", initialdir=self.setup_directory(),
                filetypes=[("Circuit setup", "*.json"), ("All files", "*")],
            )
            if not filename:
                return
            # Construct and validate everything before replacing the live circuit.
            model, rate = load_setup_file(filename)
            self.auto_running = False
            self.auto_button.configure(text="auto update")
            self.auto_status.set("Stopped")
            self.model = model
            self.auto_rate.set(str(rate))
            self.last_setup_path = Path(filename)
            if self.context_menu is not None:
                self.context_menu.destroy()
                self.context_menu = None
            self.grid_width = 2 * self.MARGIN + (model.cols - 1) * self.SPACING
            self.grid_height = 2 * self.MARGIN + (model.rows - 1) * self.SPACING
            self.canvas.configure(
                width=min(self.grid_width, 1000), height=min(self.grid_height, 640),
                scrollregion=(0, 0, self.grid_width, self.grid_height),
            )
            self.message = f"Loaded {self.last_setup_path.name}. Auto update is stopped."
            self.go_to_circuit()
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not load setup", str(error), parent=self.root)
        finally:
            # A cancelled or failed load keeps the old circuit running as before.
            if self.auto_running:
                self.schedule_auto_update()

    def cancel_auto_update(self) -> None:
        if self.auto_after_id is not None:
            self.root.after_cancel(self.auto_after_id)
            self.auto_after_id = None

    def schedule_auto_update(self) -> None:
        # Keep exactly one pending callback, including when the rate is edited.
        self.cancel_auto_update()
        try:
            rate = parse_update_rate(self.auto_rate.get())
        except ValueError:
            prefix = "Paused: " if self.auto_running else ""
            self.auto_status.set(f"{prefix}enter a rate from 0.01 to 1000.")
            return

        if self.auto_running:
            self.auto_status.set("Running")
            interval_ms = max(1, round(1000 / rate))
            self.auto_after_id = self.root.after(interval_ms, self.auto_update)
        else:
            self.auto_status.set("Stopped")

    def toggle_auto_update(self) -> None:
        self.auto_running = not self.auto_running
        self.auto_button.configure(text="stop auto update" if self.auto_running else "auto update")
        self.schedule_auto_update()

    def on_auto_rate_change(self, *_: str) -> None:
        self.schedule_auto_update()

    def auto_update(self) -> None:
        self.auto_after_id = None
        if self.auto_running:
            self.update()
            self.schedule_auto_update()

    def close(self) -> None:
        self.auto_running = False
        self.cancel_auto_update()
        self.root.destroy()


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=positive_int, default=8, help="initial view rows (default: 8)")
    parser.add_argument("--cols", type=positive_int, default=12, help="initial view columns (default: 12)")
    args = parser.parse_args()
    root = tk.Tk()
    CircuitGridApp(root, GridModel(args.rows, args.cols))
    root.mainloop()


if __name__ == "__main__":
    main()
