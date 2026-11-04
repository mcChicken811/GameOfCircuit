"""Named exports and bidirectional, synchronous imported circuits."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from circuit_grid import CircuitGridApp, GridModel, load_setup_file, save_setup_file
from test_auto_update import FakeScheduler


def source_circuit():
    circuit = GridModel(1, 2)
    outbound, internal = circuit.nodes[0]
    circuit.toggle_connection(outbound, internal)
    circuit.set_outbound(outbound, "signal")
    return circuit


class OutboundTests(unittest.TestCase):
    def test_import_side_is_editable_for_named_outbounds_and_cleared_with_name(self):
        grid = GridModel(1, 2)
        first, second = grid.nodes[0]
        with self.assertRaises(ValueError):
            grid.set_outbound_side(first, "left")
        grid.set_outbound(first, "signal")
        self.assertEqual(first.outbound_side, "right")
        grid.set_outbound_side(first, "left")
        grid.set_outbound(first, "renamed")
        self.assertEqual(first.outbound_side, "left")
        for side in ("top", "LEFT", None):
            with self.subTest(side=side), self.assertRaises(ValueError):
                grid.set_outbound_side(first, side)
        self.assertEqual(first.outbound_side, "left")
        grid.set_outbound(first, None)
        self.assertEqual(first.outbound_side, "right")
        self.assertFalse(first.is_input)
        self.assertIsNone(second.outbound_name)

    def test_names_are_unique_output_designations_and_can_be_removed(self):
        grid = GridModel(2, 2)
        first, second = grid.nodes[0]
        first.is_input = True
        grid.set_outbound(first, " result ")
        self.assertEqual(first.outbound_name, "result")
        self.assertFalse(first.is_input)
        with self.assertRaises(ValueError):
            grid.toggle_type(first)
        for invalid in ("", "   ", "result", "line\nbreak", "x" * 41):
            with self.subTest(name=invalid):
                with self.assertRaises(ValueError):
                    grid.set_outbound(second, invalid)
                self.assertIsNone(second.outbound_name)
        grid.set_outbound(first, "renamed")
        grid.set_outbound(first, None)
        grid.toggle_type(first)
        self.assertTrue(first.is_input)

    def test_outbounds_and_gate_driven_inputs_are_distinct_roles(self):
        grid = GridModel(2, 2)
        target = grid.nodes[0][1]
        grid.set_outbound(target, "result")
        with self.assertRaises(ValueError):
            grid.add_binary_gate("AND", 0, 0)
        self.assertEqual(grid.gates, [])
        grid.set_outbound(target, None)
        gate = grid.add_binary_gate("AND", 0, 0)
        with self.assertRaises(ValueError):
            grid.set_outbound(gate.target, "result")
        grid.set_outbound(gate.sources[0], "source")
        self.assertEqual(grid.outbound_nodes(), [gate.sources[0]])


class ImportedCircuitTests(unittest.TestCase):
    def test_ports_stack_independently_on_each_side_with_minimum_height(self):
        for sides in (
            ("right",), ("left",), ("left", "right"),
            ("left", "right", "left", "right"), ("right", "left", "right", "right", "left"),
        ):
            with self.subTest(sides=sides):
                source = GridModel(1, len(sides))
                for index, side in enumerate(sides):
                    node = source.nodes[0][index]
                    source.set_outbound(node, f"signal {index}")
                    source.set_outbound_side(node, side)
                parent = GridModel(1, 1)
                component = parent.import_circuit(source, -10, -10, "compact")
                self.assertEqual(component.height, max(sides.count("left"), sides.count("right")))
                expected_rows = {"left": -9, "right": -9}
                for index, (exposed, internal) in enumerate(component.ports):
                    side = sides[index]
                    self.assertEqual(exposed.row, expected_rows[side])
                    self.assertEqual(exposed.col, -10 if side == "left" else -8)
                    self.assertEqual(internal.outbound_name, f"signal {index}")
                    expected_rows[side] += 1
                self.assertEqual(GridModel.from_dict(parent.to_dict()).to_dict(), parent.to_dict())

    def test_uncovered_side_nodes_and_edges_remain_available(self):
        source = GridModel(1, 2)
        for index, node in enumerate(source.nodes[0]):
            source.set_outbound(node, f"signal {index}")
        parent = GridModel(4, 4)
        component = parent.import_circuit(source, 0, 0, "compact")
        self.assertEqual(component.height, 2)
        # The entire unused left side and the former header row are ordinary grid.
        for first, second in (
            ((0, 0), (0, 1)), ((1, 0), (2, 0)), ((0, 2), (1, 2)),
            ((1, 2), (2, 2)), ((2, 2), (3, 2)), ((1, 2), (1, 3)),
        ):
            a, b = parent.get_node(*first), parent.get_node(*second)
            self.assertTrue(parent.toggle_connection(a, b))
        parent.toggle_type(parent.nodes[1][0])
        self.assertTrue(parent.toggle_input(parent.nodes[1][0]))
        for position in ((1, 1), (2, 1)):
            with self.assertRaises(ValueError):
                parent.toggle_connection(parent.get_node(*position), parent.get_node(position[0], 0))
        self.assertIsNone(parent.component_at_node(parent.nodes[1][0]))
        self.assertEqual(GridModel.from_dict(parent.to_dict()).to_dict(), parent.to_dict())

    def test_import_preserves_preexisting_wiring_outside_the_actual_box(self):
        parent = GridModel(3, 4)
        header = (parent.nodes[0][0], parent.nodes[0][1])
        outside = (parent.nodes[1][0], parent.nodes[2][0])
        for first, second in (header, outside):
            parent.toggle_connection(first, second)
        parent.nodes[1][0].is_input = parent.nodes[1][0].value = True
        parent.import_circuit(source_circuit(), 0, 0, "compact")
        for first, second in (header, outside):
            self.assertIn(second, first.connected_nodes)
        self.assertTrue(parent.nodes[1][0].is_input)
        self.assertTrue(parent.nodes[1][0].value)

    def test_opposite_side_boxes_cannot_overlap_even_without_shared_ports(self):
        source = source_circuit()
        source.set_outbound_side(source.nodes[0][0], "left")
        parent = GridModel(4, 6)
        parent.import_circuit(source, 0, 0, "left ports")
        before = parent.to_dict()
        with self.assertRaises(ValueError):
            parent.import_circuit(source_circuit(), 0, 1, "right ports")
        self.assertEqual(parent.to_dict(), before)

    def test_left_port_and_nested_reexport_preserve_bidirectional_updates(self):
        leaf = source_circuit()
        leaf.set_outbound_side(leaf.nodes[0][0], "left")
        middle = GridModel(4, 4)
        inner = middle.import_circuit(leaf, 0, 0, "leaf")
        middle.set_outbound(inner.ports[0][0], "nested")
        middle.set_outbound_side(inner.ports[0][0], "left")
        parent = GridModel(4, 4)
        component = parent.import_circuit(middle, 0, 0, "middle")
        exposed, internal = component.ports[0]
        manual = parent.get_node(exposed.row, exposed.col - 1)
        manual.is_input = manual.value = True
        parent.toggle_connection(manual, exposed)
        parent.update()
        self.assertTrue(internal.value)
        self.assertTrue(exposed.changed_last_update)
        restored = GridModel.from_dict(parent.to_dict())
        for _ in range(5):
            self.assertEqual(parent.update(), restored.update())
            self.assertEqual(parent.to_dict(), restored.to_dict())
        parent.remove_component(component)
        self.assertEqual(exposed.connected_nodes, [manual])

    def test_outside_and_inside_nodes_influence_each_other_simultaneously(self):
        original = source_circuit()
        parent = GridModel(4, 5)
        component = parent.import_circuit(original, 0, 0, "source")
        exposed, outbound = component.ports[0]
        internal = component.circuit.nodes[0][1]
        manual = parent.nodes[1][3]
        receiver = parent.nodes[2][2]
        manual.is_input = True
        manual.value = True
        parent.toggle_connection(exposed, manual)
        parent.toggle_connection(exposed, receiver)

        self.assertEqual(parent.update(), 1)
        self.assertTrue(outbound.value)
        self.assertTrue(exposed.value)
        self.assertTrue(exposed.changed_last_update)
        self.assertFalse(internal.value)
        self.assertFalse(receiver.value)
        self.assertEqual(parent.update(), 2)
        self.assertTrue(internal.value)
        self.assertTrue(receiver.value)
        parent.toggle_input(manual)
        self.assertEqual(parent.update(), 1)
        self.assertFalse(outbound.value)
        self.assertFalse(exposed.value)
        self.assertTrue(internal.value)
        self.assertEqual(parent.update(), 2)
        self.assertFalse(internal.value)
        self.assertFalse(receiver.value)
        self.assertEqual(parent.update_count, 4)
        self.assertEqual(component.circuit.update_count, 4)
        self.assertEqual(original.update_count, 0)
        self.assertTrue(all(not n.value for n in original.all_nodes()))

    def test_repeated_imports_have_independent_state(self):
        original = source_circuit()
        parent = GridModel(4, 8)
        first = parent.import_circuit(original, 0, 0, "first")
        second = parent.import_circuit(original, 0, 4, "second")
        manual = parent.nodes[1][3]
        manual.is_input = manual.value = True
        parent.toggle_connection(first.ports[0][0], manual)
        parent.update()
        self.assertTrue(first.ports[0][1].value)
        self.assertFalse(second.ports[0][1].value)
        self.assertFalse(original.nodes[0][0].value)

    def test_imported_port_can_feed_a_gate_but_cannot_be_a_gate_target(self):
        parent = GridModel(4, 5)
        component = parent.import_circuit(source_circuit(), 0, 0, "source")
        exposed, _ = component.ports[0]
        gate = parent.add_binary_gate("OR", 1, 2)
        self.assertIn(exposed, gate.sources)
        with self.assertRaises(ValueError):
            parent.set_not_gate(parent.nodes[1][3], exposed)
        with self.assertRaises(ValueError):
            parent.toggle_type(exposed)
        parent.set_outbound(exposed, "reexport")
        self.assertEqual(parent.outbound_nodes(), [exposed])

    def test_component_body_is_reserved_and_failed_import_is_atomic(self):
        source = source_circuit()
        parent = GridModel(4, 6)
        component = parent.import_circuit(source, 0, 0, "source")
        before = parent.to_dict()
        hidden = parent.nodes[1][1]
        for action in (
            lambda: parent.import_circuit(source, 0, 1, "overlap"),
            lambda: parent.import_circuit(GridModel(1, 1), 2, 3, "no outputs"),
            lambda: parent.import_circuit(source, 3.5, 0, "invalid coordinate"),
            lambda: parent.toggle_type(hidden),
            lambda: parent.toggle_input(hidden),
            lambda: parent.set_outbound(hidden, "hidden"),
            lambda: parent.toggle_connection(hidden, parent.nodes[1][0]),
            lambda: parent.add_binary_gate("AND", 0, 0),
        ):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(parent.to_dict(), before)
        self.assertIs(parent.component_at_node(hidden), component)

    def test_nested_reexport_shares_one_value_and_cooldown_and_resets_once(self):
        leaf = GridModel(1, 1)
        leaf.set_outbound(leaf.nodes[0][0], "inner")
        middle = GridModel(3, 4)
        child = middle.import_circuit(leaf, 0, 0, "inner circuit")
        middle.set_outbound(child.ports[0][0], "outer")
        parent = GridModel(3, 5)
        component = parent.import_circuit(middle, 0, 0, "nested")
        exposed, middle_port = component.ports[0]
        leaf_port = component.circuit.components[0].ports[0][1]
        manual = parent.nodes[1][3]
        manual.is_input = manual.value = True
        parent.toggle_connection(exposed, manual)
        self.assertEqual(parent.update(), 1)
        for node in (exposed, middle_port, leaf_port):
            self.assertTrue(node.value)
            self.assertTrue(node.changed_last_update)
        self.assertEqual(parent.reset_state(), 1)
        for node in (exposed, middle_port, leaf_port):
            self.assertFalse(node.value)
            self.assertFalse(node.changed_last_update)
        self.assertTrue(manual.value)
        self.assertEqual(parent.update(), 1)

    def test_removing_component_releases_grid_nodes_and_preserves_external_wires(self):
        parent = GridModel(3, 5)
        component = parent.import_circuit(source_circuit(), 0, 0, "source")
        exposed, _ = component.ports[0]
        external = parent.nodes[1][3]
        parent.toggle_connection(exposed, external)
        parent.remove_component(component)
        self.assertEqual(parent.components, [])
        self.assertEqual(exposed.connected_nodes, [external])
        self.assertEqual(external.connected_nodes, [exposed])
        parent.toggle_type(exposed)
        self.assertTrue(exposed.is_input)

    def test_gate_inside_component_reads_old_outbound_value(self):
        child = GridModel(2, 2)
        gate = child.add_binary_gate("OR", 0, 0)
        child.set_outbound(gate.sources[0], "source")
        parent = GridModel(3, 5)
        component = parent.import_circuit(child, 0, 0, "gate circuit")
        manual = parent.nodes[1][3]
        manual.is_input = manual.value = True
        parent.toggle_connection(component.ports[0][0], manual)
        parent.update()
        internal_gate = component.circuit.gates[0]
        self.assertTrue(internal_gate.sources[0].value)
        self.assertFalse(internal_gate.target.value)
        parent.update()
        self.assertTrue(internal_gate.target.value)


class ImportedCircuitFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_version_three_imports_keep_their_original_port_positions_and_wiring(self):
        source = GridModel(1, 2)
        source.set_outbound(source.nodes[0][0], "first")
        source.set_outbound(source.nodes[0][1], "second")
        parent = GridModel(4, 5)
        component = parent.import_circuit(source, 0, 0, "legacy")
        for exposed, _ in component.ports:
            parent.toggle_connection(exposed, parent.get_node(exposed.row, exposed.col + 1))
        data = parent.to_dict()

        def omit_sides(grid):
            for row in grid["nodes"]:
                for node in row:
                    del node["outbound_side"]
            for node in grid["extra_nodes"]:
                del node["outbound_side"]
            for saved in grid["components"]:
                omit_sides(saved["circuit"])

        omit_sides(data)
        path = self.directory / "old parent.json"
        path.write_text(json.dumps({
            "format": "boolean-circuit-grid", "version": 3, "auto_rate": 2, "grid": data,
        }))
        restored, _ = load_setup_file(path)
        self.assertEqual([(node.row, node.col) for node, _ in restored.components[0].ports], [(1, 2), (2, 2)])
        self.assertEqual(restored.to_dict(), parent.to_dict())

    def test_mixed_side_parent_file_preserves_ports_state_and_external_wiring(self):
        source = GridModel(1, 3)
        for index, node in enumerate(source.nodes[0]):
            source.set_outbound(node, f"port {index}")
            source.set_outbound_side(node, "left" if index != 1 else "right")
        parent = GridModel(4, 5)
        component = parent.import_circuit(source, 0, 1, "two sides")
        for exposed, _ in component.ports:
            outside_col = exposed.col + (-1 if exposed.col == component.col else 1)
            outside = parent.get_node(exposed.row, outside_col)
            outside.is_input = outside.value = True
            parent.toggle_connection(outside, exposed)
        parent.update()
        path = self.directory / "mixed.json"
        save_setup_file(path, parent, 5)
        restored, rate = load_setup_file(path)
        self.assertEqual(rate, 5)
        self.assertEqual(parent.to_dict(), restored.to_dict())
        self.assertEqual(restored.components[0].height, 2)
        self.assertEqual([node.outbound_side for _, node in restored.components[0].ports], ["left", "right", "left"])
        for _ in range(4):
            self.assertEqual(parent.update(), restored.update())
            self.assertEqual(parent.to_dict(), restored.to_dict())

    def test_invalid_import_side_is_rejected_in_dense_and_sparse_node_records(self):
        source = source_circuit()
        for side in ("top", None, 0):
            saved = source.to_dict()
            saved["nodes"][0][0]["outbound_side"] = side
            with self.subTest(side=side), self.assertRaises(ValueError):
                GridModel.from_dict(saved)
        source.get_node(-100, 100).is_input = True
        saved = source.to_dict()
        saved["extra_nodes"][0]["outbound_side"] = "left"
        with self.assertRaises(ValueError):
            GridModel.from_dict(saved)

    def test_saved_parent_embeds_imports_and_preserves_bidirectional_continuation(self):
        source_path = self.directory / "source.json"
        save_setup_file(source_path, source_circuit(), 8)
        source, _ = load_setup_file(source_path)
        middle = GridModel(3, 4)
        nested = middle.import_circuit(source, 0, 0, "nested")
        middle.set_outbound(nested.ports[0][0], "nested signal")
        parent = GridModel(4, 6)
        component = parent.import_circuit(middle, 0, 0, "middle")
        manual = parent.nodes[1][3]
        manual.is_input = manual.value = True
        parent.toggle_connection(component.ports[0][0], manual)
        parent.update()
        path = self.directory / "parent.json"
        save_setup_file(path, parent, 2)
        source_path.unlink()
        restored, rate = load_setup_file(path)
        self.assertEqual(rate, 2)
        self.assertEqual(restored.to_dict(), parent.to_dict())
        for _ in range(4):
            self.assertEqual(restored.update(), parent.update())
            self.assertEqual(restored.to_dict(), parent.to_dict())

    def test_version_one_files_without_exports_or_components_still_load(self):
        grid = GridModel(2, 2)
        grid.toggle_connection(*grid.nodes[0])
        data = grid.to_dict()
        del data["components"]
        del data["extra_nodes"]
        for row in data["nodes"]:
            for node in row:
                del node["outbound_name"]
        path = self.directory / "old.json"
        path.write_text(json.dumps({
            "format": "boolean-circuit-grid", "version": 1, "auto_rate": 2, "grid": data,
        }), encoding="utf-8")
        restored, _ = load_setup_file(path)
        self.assertEqual(restored.to_dict(), grid.to_dict())

    def test_invalid_export_and_component_data_is_rejected(self):
        parent = GridModel(4, 6)
        parent.import_circuit(source_circuit(), 0, 0, "source")
        base = parent.to_dict()
        invalid = []
        data = copy.deepcopy(base)
        data["components"].append(copy.deepcopy(data["components"][0]))
        invalid.append(data)
        data = copy.deepcopy(base)
        data["components"][0]["row"] = 100.5
        invalid.append(data)
        data = copy.deepcopy(base)
        data["components"][0]["circuit"]["nodes"][0][0]["outbound_name"] = None
        invalid.append(data)
        data = copy.deepcopy(base)
        data["nodes"][1][2]["value"] = 1
        invalid.append(data)
        data = copy.deepcopy(base)
        data["nodes"][1][1]["value"] = 1
        invalid.append(data)
        data = copy.deepcopy(base)
        data["edges"].append([[1, 0], [1, 1]])
        invalid.append(data)
        data = GridModel(1, 2).to_dict()
        for node in data["nodes"][0]:
            node["outbound_name"] = "duplicate"
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    GridModel.from_dict(data)
        self.assertEqual(parent.to_dict(), base)


class ImportInteractionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "source.json"
        save_setup_file(self.path, source_circuit(), 2)
        self.app = CircuitGridApp.__new__(CircuitGridApp)
        self.app.model = GridModel(5, 8)
        self.app.root = FakeScheduler()
        self.app.auto_running = False
        self.app.auto_after_id = None
        self.app.auto_rate = Mock()
        self.app.auto_rate.get.return_value = "2"
        self.app.auto_status = Mock()
        self.app.auto_button = Mock()
        self.app.context_menu = None
        self.app.last_setup_path = None
        self.app.canvas = Mock()
        self.app.canvas.canvasx.side_effect = lambda x: x
        self.app.canvas.canvasy.side_effect = lambda y: y
        self.app.draw = Mock()

    def event(self, x, y):
        return SimpleNamespace(x=x, y=y, x_root=x, y_root=y)

    def invoke_menu(self, event, label):
        with patch("circuit_grid.tk.Menu") as menu_factory:
            self.app.on_right_click(event)
            for call in menu_factory.return_value.add_command.call_args_list:
                if call.kwargs["label"] == label:
                    call.kwargs["command"]()
                    return
        self.fail(f"No menu item {label!r}")

    def test_node_menu_names_renames_and_removes_outbound(self):
        node = self.app.model.nodes[0][0]
        event = self.event(*self.app.position(node))
        with patch("circuit_grid.simpledialog.askstring", return_value="clock"):
            self.invoke_menu(event, "Set outbound name...")
        self.assertEqual(node.outbound_name, "clock")
        with patch("circuit_grid.simpledialog.askstring", return_value="pulse"):
            self.invoke_menu(event, "Rename outbound...")
        self.assertEqual(node.outbound_name, "pulse")
        self.invoke_menu(event, "Remove outbound name")
        self.assertIsNone(node.outbound_name)
        self.invoke_menu(event, "Switch to input")
        self.assertTrue(node.is_input)

    def test_outbound_menu_selects_the_import_side_and_displays_current_choice(self):
        node = self.app.model.nodes[0][0]
        self.app.model.set_outbound(node, "signal")
        event = self.event(*self.app.position(node))
        self.invoke_menu(event, "Show on left when imported")
        self.assertEqual(node.outbound_side, "left")
        with patch("circuit_grid.tk.Menu") as menu_factory:
            self.app.on_right_click(event)
            options = {call.kwargs["label"]: call.kwargs for call in menu_factory.return_value.add_command.call_args_list}
            self.assertEqual(options["Show on left when imported"]["state"], "disabled")
            self.assertEqual(options["Show on right when imported"]["state"], "normal")
        self.invoke_menu(event, "Show on right when imported")
        self.assertEqual(node.outbound_side, "right")

    def test_uncovered_edges_can_be_clicked_and_only_the_visible_body_is_blocked(self):
        source = GridModel(1, 2)
        for index, node in enumerate(source.nodes[0]):
            source.set_outbound(node, f"port {index}")
        component = self.app.model.import_circuit(source, 0, 0, "compact")
        for first, second in (((0, 0), (0, 1)), ((1, 0), (2, 0)), ((1, 2), (2, 2))):
            a, b = self.app.model.get_node(*first), self.app.model.get_node(*second)
            ax, ay = self.app.position(a)
            bx, by = self.app.position(b)
            x, y = (ax + bx) / 2, (ay + by) / 2
            self.assertIsNone(self.app.component_at(x, y))
            self.app.on_left_click(self.event(x, y))
            self.assertIn(b, a.connected_nodes)
        self.assertIsNotNone(self.app.node_at(48, 112))  # Unused left-side node.
        self.assertIsNone(self.app.edge_at(63, 112))  # Edge enters the box.
        before = self.app.model.to_dict()
        self.app.on_left_click(self.event(80, 112))
        self.assertEqual(self.app.model.to_dict(), before)
        left, top, right, bottom = self.app.component_bounds(component)
        self.assertEqual(bottom - top, self.app.SPACING + 48)
        self.assertEqual((top, bottom), (88, 200))
        self.assertGreater(left, 48 + self.app.NODE_RADIUS)
        self.assertLess(right, 176 - self.app.NODE_RADIUS)

    def test_left_and_right_port_stubs_draw_toward_the_box(self):
        source = GridModel(1, 2)
        for index, node in enumerate(source.nodes[0]):
            source.set_outbound(node, f"port {index}")
        source.set_outbound_side(source.nodes[0][0], "left")
        component = self.app.model.import_circuit(source, 0, 0, "compact")
        self.app.draw_component(component)
        left, top, right, bottom = self.app.component_bounds(component)
        self.assertEqual(bottom - top, 48)
        self.assertEqual(self.app.canvas.create_line.call_args_list[0].args, (left, 112, 48, 112))
        self.assertEqual(self.app.canvas.create_line.call_args_list[1].args, (right, 112, 176, 112))

    def test_empty_space_import_exposes_ports_and_resumes_auto_update(self):
        self.app.toggle_auto_update()
        with patch("circuit_grid.filedialog.askopenfilename", return_value=str(self.path)):
            self.invoke_menu(self.event(80, 80), "Import circuit...")
        component = self.app.model.components[0]
        self.assertEqual(component.label, "source")
        exposed, internal = component.ports[0]
        self.assertEqual(internal.outbound_name, "signal")
        self.assertIs(self.app.node_at(*self.app.position(exposed)), exposed)
        self.assertIsNone(self.app.node_at(*self.app.position(self.app.model.nodes[1][1])))
        self.assertTrue(self.app.auto_running)
        self.assertEqual(len(self.app.root.pending), 1)
        before = self.app.model.to_dict()
        body_event = self.event(*self.app.position(self.app.model.nodes[1][1]))
        self.app.on_left_click(body_event)
        self.assertEqual(self.app.model.to_dict(), before)
        self.invoke_menu(body_event, "Remove imported circuit")
        self.assertEqual(self.app.model.components, [])

    def test_import_is_available_outside_grid_squares_but_not_on_edges(self):
        for event, offered in ((self.event(10, 10), True), (self.event(80, 48), False)):
            with patch("circuit_grid.tk.Menu") as menu_factory:
                self.app.on_right_click(event)
                labels = [call.kwargs["label"] for call in menu_factory.return_value.add_command.call_args_list]
                self.assertEqual("Import circuit..." in labels, offered)


if __name__ == "__main__":
    unittest.main()
