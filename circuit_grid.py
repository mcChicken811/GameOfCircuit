"""Interactive boolean-node grid. Run with: .venv/bin/python circuit_grid.py"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterator


@dataclass(eq=False)
class Node:
    """A node and its actual, mutually connected Node objects."""

    row: int
    col: int
    value: bool = False
    is_input: bool = False
    connected_nodes: list[Node] = field(default_factory=list, repr=False)
    changed_last_update: bool = False


@dataclass(eq=False)
class LogicGate:
    kind: str
    sources: tuple[Node, ...]
    target: Node
    cell: tuple[int, int] | None = None
    rotation: int = 0

    def evaluate(self) -> bool:
        if self.kind == "AND":
            return all(node.value for node in self.sources)
        if self.kind == "OR":
            return any(node.value for node in self.sources)
        return not self.sources[0].value


class GridModel:
    def __init__(self, rows: int = 8, cols: int = 12) -> None:
        if rows < 1 or cols < 1:
            raise ValueError("The grid must have at least one row and column.")
        self.rows = rows
        self.cols = cols
        self.nodes = [[Node(row, col) for col in range(cols)] for row in range(rows)]
        self.update_count = 0
        self.gates: list[LogicGate] = []
        self._original_gate_roles: dict[Node, bool] = {}

    def all_nodes(self) -> Iterator[Node]:
        for row in self.nodes:
            yield from row

    def toggle_connection(self, first: Node, second: Node) -> bool:
        """Toggle a grid edge, returning whether it is now active."""
        for node in (first, second):
            if not (
                0 <= node.row < self.rows
                and 0 <= node.col < self.cols
                and self.nodes[node.row][node.col] is node
            ):
                raise ValueError("Both nodes must belong to this grid.")
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
        if self.gate_role(node) is not None:
            raise ValueError("This node's type is set by its gate. Remove the gate to change it.")
        node.is_input = not node.is_input

    def toggle_input(self, node: Node) -> bool:
        """Only input nodes can change value directly through a click."""
        if not node.is_input or self.gate_driver(node) is not None:
            return False
        node.value = not node.value
        return True

    def gate_driver(self, node: Node) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.target is node), None)

    def gate_role(self, node: Node) -> bool | None:
        """Return the forced is_input flag, or None for an ordinary node."""
        if self.gate_driver(node) is not None:
            return True
        if any(node in gate.sources for gate in self.gates):
            return False
        return None

    def is_connected(self, node: Node) -> bool:
        return bool(node.connected_nodes) or self.gate_role(node) is not None

    def gate_in_cell(self, row: int, col: int) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.cell == (row, col)), None)

    def not_gate_on_edge(self, first: Node, second: Node) -> LogicGate | None:
        return next((gate for gate in self.gates if gate.kind == "NOT" and (
            (gate.sources[0] is first and gate.target is second)
            or (gate.sources[0] is second and gate.target is first)
        )), None)

    def add_binary_gate(self, kind: str, row: int, col: int, rotation: int = 0) -> LogicGate:
        if kind not in ("AND", "OR"):
            raise ValueError("Choose AND or OR for a grid square.")
        if not (0 <= row < self.rows - 1 and 0 <= col < self.cols - 1):
            raise ValueError("Place AND/OR gates inside a square between four grid nodes.")
        corners = (
            self.nodes[row][col], self.nodes[row][col + 1],
            self.nodes[row + 1][col + 1], self.nodes[row + 1][col],
        )
        rotation %= 4
        # Start with the two left corners as sources and upper-right as target.
        gate = LogicGate(
            kind, (corners[rotation], corners[(3 + rotation) % 4]),
            corners[(1 + rotation) % 4], (row, col), rotation,
        )
        return self._install_gate(gate, self.gate_in_cell(row, col))

    def set_not_gate(self, source: Node, target: Node) -> LogicGate:
        for node in (source, target):
            if not (
                0 <= node.row < self.rows and 0 <= node.col < self.cols
                and self.nodes[node.row][node.col] is node
            ):
                raise ValueError("Both ends of a NOT gate must belong to this grid.")
        if abs(source.row - target.row) + abs(source.col - target.col) != 1:
            raise ValueError("A NOT gate must occupy a neighbouring grid edge.")
        return self._install_gate(
            LogicGate("NOT", (source,), target), self.not_gate_on_edge(source, target)
        )

    def _install_gate(self, gate: LogicGate, replaces: LogicGate | None) -> LogicGate:
        # Validate first so an impossible placement/rotation leaves the circuit intact.
        for other in self.gates:
            if other is replaces:
                continue
            if other.target is gate.target:
                raise ValueError("A node can be driven by only one gate.")
            if other.target in gate.sources or gate.target in other.sources:
                raise ValueError("These gates would require conflicting input/output node types.")
        if replaces is not None:
            self.remove_gate(replaces, restore_wire=False)
        for node in (*gate.sources, gate.target):
            self._original_gate_roles.setdefault(node, node.is_input)
            node.is_input = node is gate.target
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
        for node in (*gate.sources, gate.target):
            if self.gate_role(node) is None:
                node.is_input = self._original_gate_roles.pop(node)
        if gate.kind == "NOT" and restore_wire:
            source = gate.sources[0]
            if gate.target not in source.connected_nodes:
                source.connected_nodes.append(gate.target)
                gate.target.connected_nodes.append(source)

    def reset_state(self) -> int:
        """Clear connected outputs' values and cooldowns; return their count."""
        reset_count = 0
        for node in self.all_nodes():
            if not node.is_input and self.is_connected(node):
                node.value = False
                node.changed_last_update = False
                reset_count += 1
        return reset_count

    def update(self) -> int:
        """Advance eligible outputs simultaneously; return the number changed.

        next value = clamp(value + sign(mean(neighbour values) - value), 0, 1)

        Use pre-update values throughout. Outputs that changed last update hold
        for this update only. Gates drive their input-type targets every update;
        manual inputs and disconnected nodes retain their values.
        """
        pending: list[tuple[Node, bool]] = []
        for node in self.all_nodes():
            if node.is_input or not node.connected_nodes or node.changed_last_update:
                continue
            # Multiplication by the positive neighbour count preserves the
            # sign of the mean difference without floating-point arithmetic.
            difference = sum(int(n.value) for n in node.connected_nodes) - (
                len(node.connected_nodes) * int(node.value)
            )
            value_change = (difference > 0) - (difference < 0)
            new_value = bool(max(0, min(1, int(node.value) + value_change)))
            if new_value != node.value:
                pending.append((node, new_value))

        # Gates also read the old state; their driven inputs have no cooldown.
        gate_values = [(gate.target, gate.evaluate()) for gate in self.gates]
        gate_changes = sum(node.value != value for node, value in gate_values)
        # Expire every previous cooldown, even for disconnected nodes and inputs.
        for node in self.all_nodes():
            node.changed_last_update = False
        # Commit together so held nodes still contribute their current values.
        for node, new_value in pending:
            node.value = new_value
            node.changed_last_update = True
        for node, new_value in gate_values:
            node.value = new_value
        self.update_count += 1
        return len(pending) + gate_changes


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

        ttk.Label(
            container,
            text="Left-click an edge: toggle connection  •  Left-click a manual input: flip 0 / 1",
        ).pack(anchor="w")
        ttk.Label(
            container,
            text="Right-click: node type  •  empty square: AND / OR  •  edge: NOT",
        ).pack(anchor="w", pady=(2, 2))
        ttk.Label(
            container,
            text="Left-click a gate: rotate  •  Arrows point to its driven IN node",
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
            scrollregion=(0, 0, self.grid_width, self.grid_height),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        horizontal = ttk.Scrollbar(grid_frame, orient="horizontal", command=self.canvas.xview)
        vertical = ttk.Scrollbar(grid_frame, orient="vertical", command=self.canvas.yview)
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.bind("<Button-1>", self.on_left_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        # macOS Tk can report a secondary click as button 2. Control-click is
        # also supported for users without a secondary mouse/trackpad button.
        if root.tk.call("tk", "windowingsystem") == "aqua":
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

    def position(self, node: Node) -> tuple[int, int]:
        return self.MARGIN + node.col * self.SPACING, self.MARGIN + node.row * self.SPACING

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
        if 0 <= row < self.model.rows - 1 and 0 <= col < self.model.cols - 1:
            return row, col
        return None

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
        for row in range(self.model.rows):
            y = self.MARGIN + row * self.SPACING
            self.canvas.create_line(
                0, y, self.grid_width, y, fill=self.GRID_COLOR, width=1
            )
        for col in range(self.model.cols):
            x = self.MARGIN + col * self.SPACING
            self.canvas.create_line(
                x, 0, x, self.grid_height, fill=self.GRID_COLOR, width=1
            )

        edges = list(self.model.active_edges())
        for first, second in edges:
            x1, y1 = self.position(first)
            x2, y2 = self.position(second)
            midpoint = ((x1 + x2) / 2, (y1 + y2) / 2)
            self.canvas.create_line(
                x1, y1, *midpoint, fill=self.value_color(first), width=5
            )
            self.canvas.create_line(
                *midpoint, x2, y2, fill=self.value_color(second), width=5
            )

        for gate in self.model.gates:
            self.draw_gate(gate)

        for node in self.model.all_nodes():
            x, y = self.position(node)
            color = self.value_color(node) if self.model.is_connected(node) else self.DISCONNECTED_COLOR
            radius = self.NODE_RADIUS
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=color, outline=color,
            )
            self.canvas.create_text(
                x, y, text=str(int(node.value)), fill="white", font=("Arial", 11, "bold")
            )
            if node.is_input:
                self.canvas.create_text(
                    x + radius + 3, y - radius - 3,
                    text="IN", anchor="sw", fill="#252a34", font=("Arial", 9, "bold"),
                )

        inputs = sum(node.is_input for node in self.model.all_nodes())
        self.status.set(
            f"Updates: {self.model.update_count}   |   Active edges: {len(edges)}   |   "
            f"Inputs: {inputs}   |   Gates: {len(self.model.gates)}   |   {self.message}"
        )

    def event_position(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def node_at(self, x: float, y: float) -> Node | None:
        col = round((x - self.MARGIN) / self.SPACING)
        row = round((y - self.MARGIN) / self.SPACING)
        if 0 <= row < self.model.rows and 0 <= col < self.model.cols:
            node = self.model.nodes[row][col]
            nx, ny = self.position(node)
            if math.hypot(x - nx, y - ny) <= self.NODE_RADIUS + 2:
                return node
        return None

    def edge_at(self, x: float, y: float) -> tuple[Node, Node] | None:
        """Find a neighbouring edge near the click, excluding the node circles."""
        if self.node_at(x, y) is not None:
            return None
        grid_x = (x - self.MARGIN) / self.SPACING
        grid_y = (y - self.MARGIN) / self.SPACING

        row, col = round(grid_y), math.floor(grid_x)
        if (
            0 <= row < self.model.rows
            and 0 <= col < self.model.cols - 1
            and abs(grid_y - row) * self.SPACING <= self.EDGE_HIT_DISTANCE
        ):
            return self.model.nodes[row][col], self.model.nodes[row][col + 1]

        row, col = math.floor(grid_y), round(grid_x)
        if (
            0 <= row < self.model.rows - 1
            and 0 <= col < self.model.cols
            and abs(grid_x - col) * self.SPACING <= self.EDGE_HIT_DISTANCE
        ):
            return self.model.nodes[row][col], self.model.nodes[row + 1][col]
        return None

    def on_left_click(self, event: tk.Event) -> None:
        x, y = self.event_position(event)
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
        node = self.node_at(x, y)
        if node is not None:
            try:
                self.model.toggle_type(node)
                kind = "input" if node.is_input else "output"
                self.message = f"Node ({node.row + 1}, {node.col + 1}) is now an {kind}."
            except ValueError as error:
                self.message = str(error)
            self.draw()
            return "break"

        if self.context_menu is not None:
            self.context_menu.destroy()
        menu = self.context_menu = tk.Menu(self.root, tearoff=False)
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
            if cell is None:
                self.message = "Place AND/OR gates inside a square between four grid nodes."
                self.draw()
                return "break"
            gate = self.model.gate_in_cell(*cell)
            rotation = gate.rotation if gate is not None else 0
            for kind in ("AND", "OR"):
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
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

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

    def cancel_auto_update(self) -> None:
        if self.auto_after_id is not None:
            self.root.after_cancel(self.auto_after_id)
            self.auto_after_id = None

    def schedule_auto_update(self) -> None:
        # Keep exactly one pending callback, including when the rate is edited.
        self.cancel_auto_update()
        try:
            rate = float(self.auto_rate.get())
            if not math.isfinite(rate) or not 0.01 <= rate <= 1000:
                raise ValueError
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
    parser.add_argument("--rows", type=positive_int, default=8, help="number of rows (default: 8)")
    parser.add_argument("--cols", type=positive_int, default=12, help="number of columns (default: 12)")
    args = parser.parse_args()
    root = tk.Tk()
    CircuitGridApp(root, GridModel(args.rows, args.cols))
    root.mainloop()


if __name__ == "__main__":
    main()
