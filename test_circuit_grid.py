"""Behaviour checks: .venv/bin/python -m unittest -v"""

import unittest

from circuit_grid import GridModel, Node


class GridModelTests(unittest.TestCase):
    def test_defaults_and_mutual_connection_toggle(self):
        grid = GridModel(2, 2)
        a, b = grid.nodes[0]
        self.assertTrue(all(not n.is_input and not n.value and not n.changed_last_update for n in grid.all_nodes()))
        self.assertTrue(grid.toggle_connection(a, b))
        self.assertEqual(a.connected_nodes, [b])
        self.assertEqual(b.connected_nodes, [a])
        self.assertEqual(list(grid.active_edges()), [(a, b)])
        self.assertFalse(grid.toggle_connection(b, a))
        self.assertEqual(a.connected_nodes, [])
        self.assertEqual(b.connected_nodes, [])

    def test_only_grid_neighbours_can_connect(self):
        grid = GridModel(2, 3)
        a = grid.nodes[0][0]
        for invalid in (a, grid.nodes[1][1], grid.nodes[0][2], Node(0, 1)):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    grid.toggle_connection(a, invalid)

    def test_input_click_and_type_changes_preserve_connections(self):
        grid = GridModel(1, 2)
        a, b = grid.nodes[0]
        grid.toggle_connection(a, b)
        self.assertFalse(grid.toggle_input(a))
        self.assertFalse(a.value)
        grid.toggle_type(a)
        self.assertTrue(grid.toggle_input(a))
        self.assertTrue(a.value)
        grid.toggle_type(a)
        self.assertTrue(a.value)
        self.assertEqual(a.connected_nodes, [b])
        grid.toggle_type(a)
        self.assertTrue(grid.toggle_input(a))
        self.assertFalse(a.value)

    def test_value_changes_by_sign_of_neighbour_average_minus_self(self):
        for value, neighbour_values, expected in (
            (False, (False, False, False), False),
            (False, (False, False, True), True),
            (False, (True, True, True), True),
            (True, (False, False, False), False),
            (True, (True, True, False), False),
            (True, (True, True, True), True),
        ):
            with self.subTest(value=value, neighbours=neighbour_values):
                grid = GridModel(2, 3)
                output = grid.nodes[0][1]
                output.value = value
                neighbours = (grid.nodes[0][0], grid.nodes[0][2], grid.nodes[1][1])
                for neighbour, neighbour_value in zip(neighbours, neighbour_values):
                    neighbour.is_input = True
                    neighbour.value = neighbour_value
                    grid.toggle_connection(output, neighbour)
                self.assertEqual(grid.update(), int(value != expected))
                self.assertIs(output.value, expected)
                self.assertEqual(tuple(n.value for n in neighbours), neighbour_values)

    def test_simultaneous_changes_then_exactly_one_update_of_cooldown(self):
        grid = GridModel(1, 2)
        first, second = grid.nodes[0]
        second.value = True
        grid.toggle_connection(first, second)
        for expected_values, changed in (
            ((True, False), 2),
            ((True, False), 0),
            ((False, True), 2),
            ((False, True), 0),
            ((True, False), 2),
        ):
            with self.subTest(update=grid.update_count + 1):
                self.assertEqual(grid.update(), changed)
                self.assertEqual((first.value, second.value), expected_values)
        self.assertEqual(grid.update_count, 5)

    def test_held_node_still_contributes_and_unchanged_nodes_remain_eligible(self):
        grid = GridModel(1, 3)
        input_node, first, second = grid.nodes[0]
        input_node.is_input = True
        input_node.value = True
        grid.toggle_connection(input_node, first)
        grid.toggle_connection(first, second)

        self.assertEqual(grid.update(), 1)
        self.assertEqual((first.value, second.value), (True, False))
        # First would flip back without its cooldown; second sees its held 1.
        self.assertEqual(grid.update(), 1)
        self.assertEqual((first.value, second.value), (True, True))

    def test_cooldown_expires_while_disconnected(self):
        grid = GridModel(1, 2)
        input_node, output = grid.nodes[0]
        input_node.is_input = True
        input_node.value = True
        grid.toggle_connection(input_node, output)
        self.assertEqual(grid.update(), 1)
        self.assertTrue(output.value)

        grid.toggle_input(input_node)
        grid.toggle_connection(input_node, output)
        self.assertEqual(grid.update(), 0)
        self.assertTrue(output.value)
        grid.toggle_connection(input_node, output)
        self.assertEqual(grid.update(), 1)
        self.assertFalse(output.value)

    def test_input_clicks_are_independent_of_cooldown(self):
        grid = GridModel(1, 2)
        input_node, output = grid.nodes[0]
        input_node.is_input = True
        output.value = True
        grid.toggle_connection(input_node, output)
        self.assertEqual(grid.update(), 1)
        self.assertFalse(output.value)

        grid.toggle_type(output)
        for expected in (True, False, True):
            self.assertTrue(grid.toggle_input(output))
            self.assertIs(output.value, expected)
        self.assertEqual(grid.update(), 0)
        self.assertTrue(output.value)
        grid.toggle_type(output)
        self.assertEqual(grid.update(), 1)
        self.assertFalse(output.value)

    def test_toggling_type_between_updates_does_not_bypass_cooldown(self):
        grid = GridModel(1, 2)
        input_node, output = grid.nodes[0]
        input_node.is_input = True
        input_node.value = True
        grid.toggle_connection(input_node, output)
        self.assertEqual(grid.update(), 1)

        grid.toggle_input(input_node)
        grid.toggle_type(output)
        grid.toggle_type(output)
        self.assertEqual(grid.update(), 0)
        self.assertTrue(output.value)
        self.assertEqual(grid.update(), 1)
        self.assertFalse(output.value)

    def test_disconnected_node_retains_either_value(self):
        for value in (False, True):
            with self.subTest(value=value):
                grid = GridModel(1, 1)
                node = grid.nodes[0][0]
                node.value = value
                self.assertEqual(grid.update(), 0)
                self.assertEqual(grid.update(), 0)
                self.assertIs(node.value, value)
                self.assertEqual(list(grid.active_edges()), [])

    def test_reset_targets_connected_outputs_and_preserves_the_circuit(self):
        grid = GridModel(2, 3)
        input_node, first, second = grid.nodes[0]
        input_node.is_input = grid.nodes[1][1].is_input = True
        grid.toggle_connection(input_node, first)
        grid.toggle_connection(first, second)
        for node in grid.all_nodes():
            node.value = True
            node.changed_last_update = True
        second.value = False  # A node already at 0 still needs its cooldown cleared.
        grid.update_count = 7
        before = [(n, n.is_input, list(n.connected_nodes)) for n in grid.all_nodes()]

        self.assertEqual(grid.reset_state(), 2)
        self.assertEqual(grid.update_count, 7)
        for node, was_input, connections in before:
            self.assertEqual(node.is_input, was_input)
            self.assertEqual(node.connected_nodes, connections)
            if node in (first, second):
                self.assertFalse(node.value)
                self.assertFalse(node.changed_last_update)
            else:
                self.assertTrue(node.value)
                self.assertTrue(node.changed_last_update)

    def test_reset_allows_immediate_update_and_future_changes_still_have_cooldown(self):
        grid = GridModel(1, 2)
        input_node, output = grid.nodes[0]
        input_node.is_input = True
        input_node.value = True
        grid.toggle_connection(input_node, output)
        grid.update()
        self.assertTrue(output.value)
        self.assertTrue(output.changed_last_update)

        grid.reset_state()
        self.assertFalse(output.value)
        self.assertEqual(grid.update(), 1)
        self.assertTrue(output.value)
        grid.toggle_input(input_node)
        self.assertEqual(grid.update(), 0)
        self.assertTrue(output.value)
        self.assertEqual(grid.update(), 1)
        self.assertFalse(output.value)


if __name__ == "__main__":
    unittest.main()
