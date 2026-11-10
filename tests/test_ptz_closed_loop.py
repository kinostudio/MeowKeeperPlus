"""Bounded closed-loop PTZ positioning from readback; synthetic motor only."""
from __future__ import annotations

import unittest

from meowkeeper.integrations.xiaomi.ptz_closed_loop import DEFAULT_MAX_PULSES, move_to, near


class Motor:
    def __init__(self, angle=50, elevation=50, steps=None, stuck=(), fail_read_after=None):
        self.position = {"angle": angle, "elevation": elevation}
        self.steps = steps or {"right": ("angle", 5), "left": ("angle", -5), "up": ("elevation", 4), "down": ("elevation", -4)}
        self.stuck, self.fail_read_after, self.reads, self.pulses = set(stuck), fail_read_after, 0, []

    def read(self):
        self.reads += 1
        if self.fail_read_after is not None and self.reads > self.fail_read_after:
            return None
        return dict(self.position)

    def pulse(self, direction):
        self.pulses.append(direction)
        axis, delta = self.steps[direction]
        if direction not in self.stuck:
            self.position[axis] = max(1, min(101, self.position[axis] + delta))
        return True


class ClosedLoopTests(unittest.TestCase):
    def test_reaches_target_within_tolerance(self):
        motor = Motor(10, 90)
        result = move_to({"angle": 80, "elevation": 20}, read=motor.read, pulse=motor.pulse)
        self.assertTrue(result.success, result)
        self.assertTrue(near(motor.position, {"angle": 80, "elevation": 20}))
        self.assertLessEqual(result.pulses, DEFAULT_MAX_PULSES)

    def test_learns_inverted_axis_from_readback(self):
        inverted = {"right": ("angle", -5), "left": ("angle", 5), "up": ("elevation", -4), "down": ("elevation", 4)}
        motor = Motor(70, 30, steps=inverted)
        result = move_to({"angle": 30, "elevation": 70}, read=motor.read, pulse=motor.pulse)
        self.assertTrue(result.success, result)
        self.assertEqual(result.learned_inverted, {"angle": True, "elevation": True})

    def test_stalled_axis_stops_without_extrapolating(self):
        motor = Motor(50, 50, stuck={"right", "left"})
        result = move_to({"angle": 90, "elevation": 50}, read=motor.read, pulse=motor.pulse)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "axis_stalled")
        self.assertEqual(result.position, {"angle": 50, "elevation": 50})
        self.assertLessEqual(len(motor.pulses), 4)

    def test_missing_readback_stops_immediately(self):
        motor = Motor(50, 50, fail_read_after=1)
        result = move_to({"angle": 90, "elevation": 50}, read=motor.read, pulse=motor.pulse)
        self.assertEqual(result.reason, "readback_lost")
        self.assertEqual(len(motor.pulses), 1)
        before = Motor(fail_read_after=0)
        self.assertEqual(move_to({"angle": 60, "elevation": 60}, read=before.read, pulse=before.pulse).reason,
                         "readback_unavailable")
        self.assertEqual(before.pulses, [])

    def test_unconfirmed_pulse_inconsistent_motion_and_bounds(self):
        self.assertEqual(move_to({"angle": 90, "elevation": 50}, read=Motor().read, pulse=lambda _d: False).reason,
                         "pulse_unconfirmed")
        erratic = Motor(50, 50)
        flips = iter([5, -5, 5, -5, 5, -5])
        def pulse(direction):
            erratic.position["angle"] += next(flips)
            return True
        self.assertIn(move_to({"angle": 90, "elevation": 50}, read=erratic.read, pulse=pulse).reason,
                      {"direction_inconsistent", "no_progress"})
        far = Motor(1, 1, steps={"right": ("angle", 1), "left": ("angle", -1), "up": ("elevation", 1), "down": ("elevation", -1)})
        result = move_to({"angle": 101, "elevation": 101}, read=far.read, pulse=far.pulse, max_pulses=10)
        self.assertEqual(result.reason, "max_pulses")
        self.assertEqual(len(far.pulses), 10)
        clock = iter([0.0, 0.0, 100.0, 100.0])
        timed = Motor(10, 10)
        self.assertEqual(move_to({"angle": 90, "elevation": 90}, read=timed.read, pulse=timed.pulse,
                                 clock=lambda: next(clock)).reason, "timeout")
        for target in ({"angle": 0, "elevation": 50}, {"angle": 50, "elevation": 102}, {"angle": "50", "elevation": 50}):
            self.assertEqual(move_to(target, read=Motor().read, pulse=Motor().pulse).reason, "invalid_target")


if __name__ == "__main__":
    unittest.main()
