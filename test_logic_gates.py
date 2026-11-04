"""Gate truth tables, port roles, simultaneous updates, and mouse routing."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from circuit_grid import CircuitGridApp, GridModel, Node


class LogicGateTests(unittest.TestCase):
    def test_and_or_truth_tables_without_gate_output_cooldown(self):
        for kind, results in (("AND", (True, False, False, False)), ("OR", (True, True, True, False))):
            grid = GridModel(2, 2)
            gate = grid.add_binary_gate(kind, 0, 0)
            for values, expected in zip(((True, True), (True, False), (False, True), (False, False)), results):
                with self.subTest(kind=kind, values=values):
                    for source, value in zip(gate.sources, values):
                        source.value = value
                    grid.update()
                    self.assertIs(gate.target.value, expected)
                    self.assertFalse(gate.target.changed_last_update)
                    self.assertTrue(gate.target.is_input)
                    self.assertTrue(all(not n.is_input for n in gate.sources))

    def test_not_truth_table_and_reversal_on_both_axes(self):
        for rows, cols in ((1, 2), (2, 1)):
            grid = GridModel(rows, cols)
            first, second = list(grid.all_nodes())
            gate = grid.set_not_gate(first, second)
            for _ in range(2):
                for value in (False, True, False):
                    gate.sources[0].value = value
                    grid.update()
                    self.assertIs(gate.target.value, not value)
                    self.assertFalse(gate.target.changed_last_update)
                gate = grid.rotate_gate(gate)
            self.assertIs(gate.sources[0], first)
            self.assertIs(gate.target, second)
            self.assertEqual(len(grid.gates), 1)

    def test_binary_rotation_uses_nearby_corners_and_restores_original_roles(self):
        grid = GridModel(2, 2)
        nw, ne = grid.nodes[0]
        sw, se = grid.nodes[1]
        nw.is_input = se.is_input = True
        before = {n: n.is_input for n in grid.all_nodes()}
        gate = grid.add_binary_gate("AND", 0, 0)
        for sources, target in (
            ((nw, sw), ne), ((ne, nw), se), ((se, ne), sw), ((sw, se), nw),
        ):
            self.assertEqual(gate.sources, sources)
            self.assertIs(gate.target, target)
            self.assertTrue(target.is_input)
            self.assertTrue(all(not n.is_input for n in sources))
            gate = grid.rotate_gate(gate)
        self.assertEqual(gate.rotation, 0)
        grid.remove_gate(gate)
        self.assertEqual({n: n.is_input for n in grid.all_nodes()}, before)

    def test_changing_kind_keeps_one_gate_and_its_orientation(self):
        grid = GridModel(2, 2)
        previous = grid.add_binary_gate("AND", 0, 0, rotation=2)
        gate = grid.add_binary_gate("OR", 0, 0, rotation=2)
        self.assertEqual(grid.gates, [gate])
        self.assertEqual(gate.sources, previous.sources)
        self.assertIs(gate.target, previous.target)

    def test_gate_and_automaton_read_the_same_old_values(self):
        grid = GridModel(2, 3)
        gate = grid.add_binary_gate("AND", 0, 1)
        source, other_source = gate.sources
        other_source.value = True
        manual_input = grid.nodes[0][0]
        manual_input.is_input = True
        manual_input.value = True
        grid.toggle_connection(manual_input, source)

        self.assertEqual(grid.update(), 1)
        self.assertTrue(source.value)
        self.assertFalse(gate.target.value)  # AND read the source's old 0.
        self.assertEqual(grid.update(), 1)
        self.assertTrue(gate.target.value)

    def test_not_replaces_normal_wire_and_drives_ordinary_neighbours(self):
        grid = GridModel(1, 3)
        source, target, neighbour = grid.nodes[0]
        grid.toggle_connection(source, target)
        grid.toggle_connection(target, neighbour)
        gate = grid.set_not_gate(source, target)
        self.assertEqual(source.connected_nodes, [])
        self.assertEqual(target.connected_nodes, [neighbour])
        self.assertTrue(grid.is_connected(source))

        grid.update()
        self.assertTrue(target.value)
        self.assertFalse(source.value)  # Gate output does not feed back through a wire.
        self.assertFalse(neighbour.value)
        grid.update()
        self.assertTrue(neighbour.value)
        with self.assertRaises(ValueError):
            grid.toggle_connection(source, target)
        grid.remove_gate(gate)
        self.assertIn(target, source.connected_nodes)
        self.assertIn(source, target.connected_nodes)
        self.assertFalse(target.is_input)

    def test_gate_port_roles_are_reserved_and_target_is_not_click_controlled(self):
        grid = GridModel(2, 2)
        gate = grid.add_binary_gate("OR", 0, 0)
        for node in (*gate.sources, gate.target):
            with self.assertRaises(ValueError):
                grid.toggle_type(node)
        self.assertFalse(grid.toggle_input(gate.target))
        self.assertFalse(gate.target.value)

    def test_shared_sources_restore_their_type_only_after_last_gate_is_removed(self):
        grid = GridModel(3, 2)
        shared = grid.nodes[1][0]
        shared.is_input = True
        first = grid.add_binary_gate("AND", 0, 0)
        second = grid.add_binary_gate("OR", 1, 0)
        grid.remove_gate(first)
        self.assertFalse(shared.is_input)
        grid.remove_gate(second)
        self.assertTrue(shared.is_input)

    def test_conflicting_placement_or_rotation_leaves_existing_gates_intact(self):
        grid = GridModel(3, 3)
        first = grid.add_binary_gate("AND", 0, 0)
        second = grid.add_binary_gate("OR", 1, 0)
        before = [(n, n.is_input, list(n.connected_nodes)) for n in grid.all_nodes()]
        with self.assertRaises(ValueError):
            grid.add_binary_gate("AND", 0, 1)  # Would read first's driven input node.
        with self.assertRaises(ValueError):
            grid.rotate_gate(first)  # Would also drive second's target.
        self.assertEqual(grid.gates, [first, second])
        self.assertEqual([(n, n.is_input, n.connected_nodes) for n in grid.all_nodes()], before)

    def test_invalid_placements_do_not_create_gates(self):
        grid = GridModel(2, 3)
        for row, col in ((-1, 0), (1, 0), (0, 2)):
            with self.assertRaises(ValueError):
                grid.add_binary_gate("AND", row, col)
        for target in (grid.nodes[1][1], grid.nodes[0][2], Node(0, 1)):
            with self.assertRaises(ValueError):
                grid.set_not_gate(grid.nodes[0][0], target)
        self.assertEqual(grid.gates, [])

    def test_reset_includes_gate_sources_and_preserves_driven_input_until_update(self):
        grid = GridModel(2, 2)
        gate = grid.add_binary_gate("AND", 0, 0)
        for source in gate.sources:
            source.value = True
            source.changed_last_update = True
        gate.target.value = True
        self.assertEqual(grid.reset_state(), 2)
        self.assertTrue(all(not n.value and not n.changed_last_update for n in gate.sources))
        self.assertTrue(gate.target.value)
        grid.update()
        self.assertFalse(gate.target.value)


class GateInteractionTests(unittest.TestCase):
    def setUp(self):
        self.app = CircuitGridApp.__new__(CircuitGridApp)
        self.app.model = GridModel(3, 3)
        self.app.root = Mock()
        self.app.context_menu = None
        self.app.canvas = Mock()
        self.app.canvas.canvasx.side_effect = lambda x: x
        self.app.canvas.canvasy.side_effect = lambda y: y
        self.app.draw = Mock()

    def event(self, x, y):
        return SimpleNamespace(x=x, y=y, x_root=x, y_root=y)

    def invoke_menu(self, event, label):
        with patch("circuit_grid.tk.Menu") as menu_factory:
            self.assertEqual(self.app.on_right_click(event), "break")
            for call in menu_factory.return_value.add_command.call_args_list:
                if call.kwargs["label"] == label:
                    call.kwargs["command"]()
                    return
        self.fail(f"Menu item {label!r} was not offered")

    def test_empty_square_menu_places_gate_and_left_click_rotates(self):
        centre = self.app.MARGIN + self.app.SPACING / 2
        event = self.event(centre, centre)
        self.invoke_menu(event, "Place AND gate")
        self.assertEqual(self.app.model.gates[0].kind, "AND")
        self.app.on_left_click(event)
        self.assertEqual(self.app.model.gates[0].rotation, 1)
        self.invoke_menu(event, "Use OR gate")
        self.assertEqual(self.app.model.gates[0].kind, "OR")
        self.assertEqual(self.app.model.gates[0].rotation, 1)
        self.invoke_menu(event, "Remove gate")
        self.assertEqual(self.app.model.gates, [])

    def test_not_menu_rotation_and_removal_at_edge_of_gate_body(self):
        x = self.app.MARGIN + self.app.SPACING / 2
        y = self.app.MARGIN
        self.invoke_menu(self.event(x, y), "NOT: left → right")
        self.app.on_left_click(self.event(x, y))
        self.assertIs(self.app.model.gates[0].target, self.app.model.nodes[0][0])
        # The badge extends beyond the thin edge's click tolerance.
        self.invoke_menu(self.event(x, y + 9), "Make normal edge (remove NOT)")
        self.assertEqual(self.app.model.gates, [])
        self.assertEqual(len(list(self.app.model.active_edges())), 1)


if __name__ == "__main__":
    unittest.main()
