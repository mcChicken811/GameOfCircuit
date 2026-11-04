"""Unbounded coordinates, sparse persistence, and scrolling/hit testing."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from circuit_grid import CircuitGridApp, GridModel, Node, load_setup_file, save_setup_file


class InfiniteModelTests(unittest.TestCase):
    def test_distant_nodes_in_every_direction_keep_identity_and_update(self):
        grid = GridModel(1, 1)
        for row, col in ((-10000, -10000), (-10000, 10000), (10000, -10000), (10000, 10000)):
            source = grid.get_node(row, col)
            target = grid.get_node(row, col + 1)
            self.assertIs(source, grid.get_node(row, col))
            source.is_input = source.value = True
            grid.toggle_connection(source, target)
        self.assertEqual(len(list(grid.all_nodes())), 9)
        self.assertEqual(grid.update(), 4)
        self.assertEqual(grid.update(), 0)
        for source, target in grid.active_edges():
            self.assertTrue(target.value)
            self.assertFalse(target.changed_last_update)
            grid.toggle_input(source)
        self.assertEqual(grid.update(), 4)
        with self.assertRaises(ValueError):
            grid.toggle_connection(grid.get_node(-10000, -10000), Node(-10000, -9999))

    def test_gates_can_cross_the_origin_and_rotate_far_from_it(self):
        grid = GridModel(1, 1)
        for row, col in ((-1, -1), (-500, 500), (500, -500), (500, 500)):
            gate = grid.add_binary_gate("NOR", row, col)
            for _ in range(4):
                for source in gate.sources:
                    source.value = False
                grid.update()
                self.assertTrue(gate.target.value)
                gate = grid.rotate_gate(gate)
            self.assertEqual(gate.cell, (row, col))
        source, target = grid.get_node(-100, -1), grid.get_node(-100, 0)
        grid.toggle_connection(source, target)
        gate = grid.set_not_gate(source, target)
        grid.update()
        self.assertTrue(target.value)
        grid.remove_gate(gate)
        self.assertEqual(source.connected_nodes, [target])

    def test_far_component_retains_bidirectional_feedback_after_save(self):
        child = GridModel(1, 1)
        inside = child.get_node(-2000, 8000)
        child.set_outbound(inside, "remote")
        neighbour = child.get_node(-2000, 8001)
        child.toggle_connection(inside, neighbour)
        parent = GridModel(1, 1)
        component = parent.import_circuit(child, -9000, 4000, "distant circuit")
        port, _ = component.ports[0]
        manual = parent.get_node(port.row, port.col + 1)
        manual.is_input = manual.value = True
        parent.toggle_connection(port, manual)
        self.assertEqual(parent.update(), 1)
        self.assertTrue(port.changed_last_update)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "infinite.json"
            save_setup_file(path, parent, 3.5)
            self.assertLess(path.stat().st_size, 6000)
            self.assertEqual(json.loads(path.read_text())["version"], 4)
            restored, rate = load_setup_file(path)
        self.assertEqual(rate, 3.5)
        self.assertEqual(parent.to_dict(), restored.to_dict())
        for _ in range(4):
            self.assertEqual(parent.update(), restored.update())
            self.assertEqual(parent.to_dict(), restored.to_dict())
        self.assertEqual(parent.reset_state(), restored.reset_state())
        self.assertEqual(parent.to_dict(), restored.to_dict())

    def test_sparse_save_keeps_disconnected_values_inputs_and_gate_roles(self):
        grid = GridModel(1, 1)
        detached = grid.get_node(-1000, 0)
        detached.value = detached.changed_last_update = True
        gate = grid.add_binary_gate("NAND", 9999, -9999)
        grid.toggle_type(gate.sources[0])
        grid.set_outbound(grid.get_node(0, -1000), "left")
        grid.set_outbound(grid.get_node(-1000, 50), "upper")
        grid.get_node(900000, 900000)  # Untouched default nodes need no saved entry.
        saved = grid.to_dict()
        self.assertEqual([node.outbound_name for node in grid.outbound_nodes()], ["upper", "left"])
        restored = GridModel.from_dict(saved)
        self.assertEqual(restored.to_dict(), saved)
        self.assertNotIn((900000, 900000), restored.extra_nodes)
        self.assertTrue(restored.get_node(-1000, 0).changed_last_update)
        restored.remove_gate(restored.gates[0])
        self.assertTrue(restored.get_node(9999, -9999).is_input)

    def test_legacy_version_two_loads_and_can_expand_in_negative_direction(self):
        legacy = GridModel(2, 2)
        gate = legacy.add_binary_gate("AND", 0, 0)
        gate.sources[0].value = True
        data = legacy.to_dict()
        del data["extra_nodes"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps({
                "format": "boolean-circuit-grid", "version": 2, "auto_rate": 2, "grid": data,
            }))
            restored, _ = load_setup_file(path)
        restored.toggle_connection(restored.get_node(0, -1), restored.get_node(0, 0))
        restored.update()
        self.assertTrue(restored.get_node(0, -1).value)

    def test_sparse_node_records_reject_bad_coordinates_duplicates_and_state(self):
        grid = GridModel(1, 1)
        grid.get_node(-4, 2).value = True
        base = grid.to_dict()
        invalid = []
        for key, value in (("row", True), ("col", 1.5), ("value", 2), ("base_is_input", 0)):
            data = copy.deepcopy(base)
            data["extra_nodes"][0][key] = value
            invalid.append(data)
        data = copy.deepcopy(base)
        data["extra_nodes"] *= 2
        invalid.append(data)
        data = copy.deepcopy(base)
        data["extra_nodes"][0].update(row=0, col=0)
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                GridModel.from_dict(data)


class InfiniteViewportTests(unittest.TestCase):
    def setUp(self):
        self.app = CircuitGridApp.__new__(CircuitGridApp)
        self.app.model = GridModel(2, 2)
        self.app.root = Mock()
        self.app.context_menu = None
        self.app.canvas = Mock()
        self.app.status = Mock()
        self.app.message = "Ready"
        self.app.windowing_system = "win32"
        self.app.grid_width = self.app.grid_height = 160
        self.offset = [0, 0]
        self.app.canvas.canvasx.side_effect = lambda x: self.offset[0] + x
        self.app.canvas.canvasy.side_effect = lambda y: self.offset[1] + y
        self.app.canvas.winfo_width.return_value = 640
        self.app.canvas.winfo_height.return_value = 480
        self.app.canvas.xview_scroll.side_effect = lambda n, units: self.pan(0, n)
        self.app.canvas.yview_scroll.side_effect = lambda n, units: self.pan(1, n)
        self.app.canvas.xview_moveto.side_effect = lambda fraction: self.offset.__setitem__(0, fraction * 160)
        self.app.canvas.yview_moveto.side_effect = lambda fraction: self.offset.__setitem__(1, fraction * 160)

    def pan(self, axis, distance):
        self.offset[axis] += distance

    def event(self, x=0, y=0, *, delta=0, state=0, num=0):
        return SimpleNamespace(x=x, y=y, x_root=x, y_root=y, delta=delta, state=state, num=num)

    def test_wheel_shift_wheel_trackpad_and_linux_buttons(self):
        with patch.object(self.app, "draw") as draw:
            self.assertEqual(self.app.on_scroll(self.event(delta=120)), "break")
            self.assertEqual(self.offset, [0, -40])
            self.app.on_scroll(self.event(delta=-120))
            self.app.on_scroll(self.event(delta=120, state=1))
            self.assertEqual(self.offset, [-40, 0])
            self.app.windowing_system = "aqua"
            self.app.on_scroll(self.event(delta=-3), horizontal=True)
            self.assertEqual(self.offset, [-16, 0])
            self.app.on_scroll(self.event(num=4))
            self.app.on_scroll(self.event(num=5, state=1))
            self.assertEqual(self.offset, [24, -40])
            self.assertEqual(draw.call_count, 6)
            self.app.on_scroll(self.event())
            self.assertEqual(draw.call_count, 6)

    def test_scrolling_empty_space_draws_only_visible_nodes_and_allocates_none(self):
        before = self.app.model.to_dict()
        for x, y in ((0, 0), (-640000, -640000), (640000, 640000), (640000, -640000)):
            self.offset[:] = [x, y]
            self.app.canvas.create_oval.reset_mock()
            self.app.draw()
            self.assertGreater(self.app.canvas.create_oval.call_count, 50)
            self.assertLess(self.app.canvas.create_oval.call_count, 250)
            self.assertEqual(self.app.model.extra_nodes, {})
            self.assertEqual(self.app.model.to_dict(), before)

    def test_clicks_after_panning_edit_the_correct_world_nodes_and_gates(self):
        self.offset[:] = [-64000, 128000]
        self.app.on_left_click(self.event(80, 48))
        first = self.app.model.get_node(2000, -1000)
        second = self.app.model.get_node(2000, -999)
        self.assertEqual(first.connected_nodes, [second])
        with patch("circuit_grid.tk.Menu") as menu_factory:
            self.app.on_right_click(self.event(80, 80))
            for call in menu_factory.return_value.add_command.call_args_list:
                if call.kwargs["label"] == "Place NOR gate":
                    call.kwargs["command"]()
                    break
            else:
                self.fail("Gate menu missing after panning")
        self.assertEqual(self.app.model.gates[0].cell, (2000, -1000))
        self.app.on_left_click(self.event(80, 80))
        self.assertEqual(self.app.model.gates[0].rotation, 1)
        self.offset[:] = [0, 0]
        self.app.draw()
        self.assertEqual(first.connected_nodes, [second])

    def test_offscreen_nodes_keep_updating_and_reappear_with_current_state(self):
        first, second = self.app.model.nodes[0]
        first.is_input = first.value = True
        self.app.model.toggle_connection(first, second)
        self.offset[:] = [-640000, 640000]
        self.app.update()
        self.assertTrue(second.value)
        self.app.canvas.create_oval.reset_mock()
        self.offset[:] = [0, 0]
        self.app.draw()
        colors = [call.kwargs["fill"] for call in self.app.canvas.create_oval.call_args_list]
        self.assertEqual(colors.count(self.app.ONE_COLOR), 2)

    def test_go_to_circuit_returns_to_remote_content_or_empty_origin(self):
        self.app.model.get_node(-1000, 2000).is_input = True
        self.app.go_to_circuit()
        self.assertEqual(self.offset, [128000, -64000])
        self.app.model = GridModel(1, 1)
        self.app.go_to_circuit()
        self.assertEqual(self.offset, [0, 0])


if __name__ == "__main__":
    unittest.main()
