"""Check timer behaviour without opening a GUI window."""

import unittest
from unittest.mock import Mock

from circuit_grid import CircuitGridApp, GridModel


class FakeScheduler:
    def __init__(self):
        self.pending = {}
        self.next_id = 0
        self.destroyed = False

    def after(self, delay_ms, callback):
        self.next_id += 1
        timer_id = f"after#{self.next_id}"
        self.pending[timer_id] = (delay_ms, callback)
        return timer_id

    def after_cancel(self, timer_id):
        del self.pending[timer_id]

    def fire_next(self):
        timer_id = next(iter(self.pending))
        _, callback = self.pending.pop(timer_id)
        callback()

    def destroy(self):
        self.destroyed = True


class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        self.app = CircuitGridApp.__new__(CircuitGridApp)
        self.app.root = FakeScheduler()
        self.app.auto_running = False
        self.app.auto_after_id = None
        self.app.auto_rate = Mock()
        self.app.auto_rate.get.return_value = "2"
        self.app.auto_status = Mock()
        self.app.auto_button = Mock()
        self.app.model = GridModel(1, 2)
        first, second = self.app.model.nodes[0]
        second.value = True
        self.app.model.toggle_connection(first, second)
        # Exercise the real simulation while omitting drawing in these timer tests.
        self.app.update = self.app.model.update

    def assert_one_timer(self, delay_ms):
        self.assertEqual(len(self.app.root.pending), 1)
        self.assertEqual(next(iter(self.app.root.pending.values()))[0], delay_ms)

    def test_auto_steps_preserve_cooldown_and_allow_manual_updates(self):
        self.app.toggle_auto_update()
        self.assert_one_timer(500)
        self.assertEqual(self.app.model.update_count, 0)
        for expected_count in (1, 2):
            self.app.root.fire_next()
            self.assertEqual(self.app.model.update_count, expected_count)
            self.assertEqual([n.value for n in self.app.model.nodes[0]], [True, False])
            self.assert_one_timer(500)
        self.app.update()
        self.assertEqual([n.value for n in self.app.model.nodes[0]], [False, True])
        self.assert_one_timer(500)

    def test_stopping_cancels_pending_updates_and_can_restart(self):
        self.app.toggle_auto_update()
        _, callback = next(iter(self.app.root.pending.values()))
        self.app.toggle_auto_update()
        self.assertFalse(self.app.auto_running)
        self.assertEqual(self.app.root.pending, {})
        callback()  # A late callback must not advance a stopped simulation.
        self.assertEqual(self.app.model.update_count, 0)
        self.assertEqual(self.app.root.pending, {})
        self.app.toggle_auto_update()
        self.assert_one_timer(500)

    def test_rate_edit_replaces_timer_without_duplicating_updates(self):
        self.app.toggle_auto_update()
        old_timer = self.app.auto_after_id
        self.app.auto_rate.get.return_value = "8"
        self.app.on_auto_rate_change()
        self.assertNotIn(old_timer, self.app.root.pending)
        self.assert_one_timer(125)
        self.app.root.fire_next()
        self.assertEqual(self.app.model.update_count, 1)
        self.assert_one_timer(125)

    def test_invalid_rate_pauses_and_valid_decimal_rate_resumes(self):
        self.app.toggle_auto_update()
        for invalid in ("", "abc", "0", "-1", "nan", "inf", "1001", "0.001"):
            with self.subTest(rate=invalid):
                self.app.auto_rate.get.return_value = invalid
                self.app.on_auto_rate_change()
                self.assertEqual(self.app.root.pending, {})
                self.assertTrue(self.app.auto_running)
                self.assertIn("Paused", self.app.auto_status.set.call_args.args[0])
                self.assertEqual(self.app.model.update_count, 0)
        self.app.auto_rate.get.return_value = "0.5"
        self.app.on_auto_rate_change()
        self.assert_one_timer(2000)
        self.app.root.fire_next()
        self.assertEqual(self.app.model.update_count, 1)

    def test_closing_cancels_the_timer_before_destroying_the_window(self):
        self.app.toggle_auto_update()
        self.app.close()
        self.assertEqual(self.app.root.pending, {})
        self.assertFalse(self.app.auto_running)
        self.assertTrue(self.app.root.destroyed)


if __name__ == "__main__":
    unittest.main()
