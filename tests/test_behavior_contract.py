from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.domain import BehaviorState, EventTag, MotionState


class BehaviorContractTests(unittest.TestCase):
    def test_behavior_values_are_stable(self) -> None:
        self.assertEqual(
            {item.value for item in BehaviorState},
            {
                "sleeping",
                "resting",
                "eating",
                "drinking",
                "playing",
                "grooming",
                "litter_box",
                "other_active",
                "out_of_view",
                "unknown",
            },
        )

    def test_motion_values_are_not_behavior_values(self) -> None:
        behavior_values = {item.value for item in BehaviorState}
        motion_values = {item.value for item in MotionState}
        self.assertEqual(
            motion_values,
            {"stationary", "low_motion", "walking", "running", "jumping"},
        )
        self.assertTrue(behavior_values.isdisjoint(motion_values))
        self.assertNotIn("running", behavior_values)

    def test_event_tags_are_stable(self) -> None:
        self.assertEqual(
            {item.value for item in EventTag},
            {
                "chasing",
                "cat_interaction",
                "meow",
                "caterwaul",
                "retching",
                "possible_vomit",
                "zone_enter",
                "zone_exit",
            },
        )

    def test_documentation_contains_every_contract_value(self) -> None:
        content = (ROOT / "docs" / "BEHAVIOR_MODEL.md").read_text(encoding="utf-8")
        for enum_type in (BehaviorState, MotionState, EventTag):
            for item in enum_type:
                self.assertIn(f"`{item.value}`", content)


if __name__ == "__main__":
    unittest.main()
