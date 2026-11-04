"""File round trips, validation, and save/load button behaviour."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from circuit_grid import CircuitGridApp, GridModel, load_setup_file, save_setup_file
from test_auto_update import FakeScheduler


class SetupFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "my circuit.json"

    def mixed_grid(self):
        grid = GridModel(4, 8)
        for node in grid.all_nodes():
            node.is_input = (node.row + node.col) % 3 == 0
            node.value = (node.row + node.col) % 2 == 1
            node.changed_last_update = (node.row * grid.cols + node.col) % 3 == 0
        grid.add_binary_gate("AND", 0, 0, rotation=1)
        grid.add_binary_gate("OR", 2, 0, rotation=2)
        grid.add_binary_gate("NAND", 2, 3, rotation=3)
        grid.add_binary_gate("NOR", 2, 6, rotation=1)
        grid.set_not_gate(grid.nodes[1][4], grid.nodes[1][5])
        for first, second in (
            ((0, 2), (0, 1)), ((1, 1), (1, 2)), ((2, 2), (2, 1)),
            ((0, 4), (1, 4)), ((1, 5), (2, 5)),
        ):
            grid.toggle_connection(grid.nodes[first[0]][first[1]], grid.nodes[second[0]][second[1]])
        grid.update_count = 11
        return grid

    def test_full_file_round_trip_preserves_future_updates_and_gate_removal(self):
        grid = self.mixed_grid()
        save_setup_file(self.path, grid, "3.5")
        restored, rate = load_setup_file(self.path)
        self.assertEqual(rate, 3.5)
        self.assertEqual(restored.to_dict(), grid.to_dict())
        self.assertEqual(
            [node.is_input for node in restored.all_nodes()],
            [node.is_input for node in grid.all_nodes()],
        )
        for _ in range(6):
            self.assertEqual(restored.update(), grid.update())
            self.assertEqual(restored.to_dict(), grid.to_dict())
        for original_gate, loaded_gate in zip(list(grid.gates), list(restored.gates)):
            grid.remove_gate(original_gate)
            restored.remove_gate(loaded_gate)
        self.assertEqual(restored.to_dict(), grid.to_dict())

    def test_shared_gate_source_preserves_editable_type_through_save_and_load(self):
        grid = GridModel(3, 2)
        grid.nodes[1][0].is_input = True
        grid.add_binary_gate("AND", 0, 0)
        grid.add_binary_gate("OR", 1, 0)
        grid.toggle_type(grid.nodes[1][0])
        save_setup_file(self.path, grid, 2)
        restored, _ = load_setup_file(self.path)
        first, second = restored.gates
        self.assertFalse(restored.nodes[1][0].is_input)
        restored.toggle_type(restored.nodes[1][0])
        restored.remove_gate(first)
        self.assertTrue(restored.nodes[1][0].is_input)
        restored.remove_gate(second)
        self.assertTrue(restored.nodes[1][0].is_input)

    def test_direct_gate_chain_survives_save_and_load(self):
        grid = GridModel(1, 3)
        first, middle, last = grid.nodes[0]
        first.is_input = True
        grid.set_not_gate(middle, last)
        grid.set_not_gate(first, middle)
        save_setup_file(self.path, grid, 2)
        restored, _ = load_setup_file(self.path)
        self.assertTrue(restored.nodes[0][0].is_input)
        self.assertTrue(restored.nodes[0][1].is_input)
        for _ in range(3):
            self.assertEqual(restored.update(), grid.update())
            self.assertEqual(restored.to_dict(), grid.to_dict())

    def test_rejects_invalid_nodes_edges_and_gates(self):
        grid = GridModel(3, 3)
        grid.toggle_connection(grid.nodes[0][0], grid.nodes[0][1])
        base = grid.to_dict()
        cases = []
        for key, value in (("rows", True), ("cols", -2), ("nodes", []), ("gates", None)):
            data = copy.deepcopy(base)
            data[key] = value
            cases.append(data)
        for key, value in (("value", 2), ("base_is_input", "false"), ("changed_last_update", 1)):
            data = copy.deepcopy(base)
            data["nodes"][0][0][key] = value
            cases.append(data)
        for edge in ([[0, 1], [0, 0]], [[0, 0], [2, 2]], [[0, 0], [20, 0]]):
            data = copy.deepcopy(base)
            data["edges"].append(edge)
            cases.append(data)
        for gates in (
            [{"kind": "XOR", "cell": [0, 0], "rotation": 0}],
            [{"kind": "AND", "cell": [0, 0], "rotation": 4}],
            [{"kind": "AND", "cell": [2.5, 2], "rotation": 0}],
            [{"kind": "AND", "cell": [0, 0], "rotation": 0}] * 2,
            [
                {"kind": "AND", "cell": [0, 0], "rotation": 0},
                {"kind": "OR", "cell": [0, 1], "rotation": 3},
            ],
            [{"kind": "NOT", "source": [0, 0], "target": [0, 1]}],
            [{"kind": "NOT", "source": [1, 0], "target": [1, 1]}] * 2,
        ):
            data = copy.deepcopy(base)
            data["gates"] = gates
            cases.append(data)
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    GridModel.from_dict(data)
        self.assertEqual(grid.to_dict(), base)

    def test_bad_json_and_unsupported_versions_are_rejected(self):
        for content in (
            '{"grid":', "[]", "{}",
            '{"format": "boolean-circuit-grid", "version": 5}',
            '{"format": "boolean-circuit-grid", "version": true}',
        ):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_setup_file(self.path)

    def test_invalid_rate_cannot_overwrite_an_existing_save_or_load(self):
        save_setup_file(self.path, GridModel(1, 1), 2)
        original = self.path.read_text(encoding="utf-8")
        for rate in ("", "nan", "inf", 0, -1, True, 1001):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    save_setup_file(self.path, GridModel(2, 2), rate)
                self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        payload = json.loads(original)
        payload["auto_rate"] = -1
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_setup_file(self.path)

    def test_failed_atomic_write_keeps_existing_file_and_removes_temporary_file(self):
        save_setup_file(self.path, GridModel(1, 1), 2)
        original = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("Write denied")):
            with self.assertRaises(OSError):
                save_setup_file(self.path, self.mixed_grid(), 5)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])


class SetupButtonTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "chosen setup.json"
        self.app = CircuitGridApp.__new__(CircuitGridApp)
        self.app.root = FakeScheduler()
        self.app.model = GridModel(2, 2)
        self.app.auto_running = False
        self.app.auto_after_id = None
        self.app.auto_rate = Mock()
        self.app.auto_rate.get.return_value = "2"
        self.app.auto_rate.set.side_effect = lambda value: setattr(
            self.app.auto_rate.get, "return_value", value
        )
        self.app.auto_status = Mock()
        self.app.auto_button = Mock()
        self.app.last_setup_path = None
        self.app.context_menu = Mock()
        self.app.canvas = Mock()
        self.app.draw = Mock()
        self.app.toggle_auto_update()

    def test_save_uses_chosen_path_and_resumes_auto_after_dialog(self):
        def choose_path(**kwargs):
            self.assertEqual(self.app.root.pending, {})
            return str(self.path)

        with patch("circuit_grid.filedialog.asksaveasfilename", side_effect=choose_path):
            self.app.save_setup()
        restored, rate = load_setup_file(self.path)
        self.assertEqual(restored.to_dict(), self.app.model.to_dict())
        self.assertEqual(rate, 2)
        self.assertTrue(self.app.auto_running)
        self.assertEqual(len(self.app.root.pending), 1)
        self.assertEqual(self.app.last_setup_path, self.path)

    def test_loading_restores_grid_size_and_rate_and_leaves_auto_stopped(self):
        saved = GridModel(3, 5)
        saved.add_binary_gate("OR", 0, 0, rotation=3)
        save_setup_file(self.path, saved, 0.123456789)
        with patch("circuit_grid.filedialog.askopenfilename", return_value=str(self.path)):
            self.app.load_setup()
        self.assertEqual(self.app.model.to_dict(), saved.to_dict())
        self.assertEqual(self.app.auto_rate.get(), "0.123456789")
        self.assertFalse(self.app.auto_running)
        self.assertEqual(self.app.root.pending, {})
        self.assertIsNone(self.app.context_menu)
        self.assertEqual(self.app.grid_width, 2 * self.app.MARGIN + 4 * self.app.SPACING)
        self.assertEqual(self.app.grid_height, 2 * self.app.MARGIN + 2 * self.app.SPACING)
        self.app.canvas.xview_moveto.assert_called_with(0)
        self.app.canvas.yview_moveto.assert_called_with(0)

    def test_cancelled_or_bad_load_preserves_current_circuit_and_running_timer(self):
        self.path.write_text("not JSON", encoding="utf-8")
        original = self.app.model
        state = original.to_dict()
        for filename in ("", str(self.path)):
            with self.subTest(filename=filename):
                with (
                    patch("circuit_grid.filedialog.askopenfilename", return_value=filename),
                    patch("circuit_grid.messagebox.showerror") as showerror,
                ):
                    self.app.load_setup()
                    self.assertEqual(showerror.call_count, int(bool(filename)))
                self.assertIs(self.app.model, original)
                self.assertEqual(original.to_dict(), state)
                self.assertTrue(self.app.auto_running)
                self.assertEqual(len(self.app.root.pending), 1)
                self.assertIsNone(self.app.last_setup_path)


if __name__ == "__main__":
    unittest.main()
