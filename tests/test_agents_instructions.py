from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentsInstructionsTests(unittest.TestCase):
    def test_agents_file_is_compact_and_routes_to_all_spec_documents(self) -> None:
        content = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        # 使用者追加了 GitHub 授權邊界；保留精簡上限並容納該必要段落。
        self.assertLessEqual(len(content.splitlines()), 80)
        self.assertIn("## GitHub Authorization Boundary", content)
        self.assertIn("kinostudio/MeowKeeperPlus", content)
        self.assertIn("## Coding Principles", content)
        self.assertIn("不得捏造硬體、帳密與外部狀態", content)
        self.assertIn("較低 reasoning", content)
        self.assertIn("MCP、瀏覽器或 subagent", content)
        for document in (
            "PRD.md",
            "ARCHITECTURE.md",
            "BEHAVIOR_MODEL.md",
            "SECURITY.md",
            "COMPATIBILITY.md",
            "ROADMAP.md",
            "TESTING.md",
        ):
            self.assertIn(document, content)


if __name__ == "__main__":
    unittest.main()
