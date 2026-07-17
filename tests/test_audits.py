from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit_runtime import audit

ROOT = Path(__file__).resolve().parents[1]


class RuntimeAuditTests(unittest.TestCase):
    def test_repository_runtime_boundary(self) -> None:
        self.assertEqual(audit(ROOT), [])

    def test_forbidden_import_and_artifact_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "src" / "demo"
            package.mkdir(parents=True)
            (package / "bad.py").write_text("import jpype\n", encoding="utf-8")
            (package / "bad.jar").write_bytes(b"not a jar")
            (root / "pyproject.toml").write_text(
                '[project]\ndependencies = ["jpype"]\n'
                '[project.optional-dependencies]\nreasoning = ["mowl"]\n',
                encoding="utf-8",
            )
            errors = audit(root)
        self.assertTrue(any("forbidden imports" in error for error in errors))
        self.assertTrue(any("forbidden runtime artifact" in error for error in errors))
        self.assertTrue(any("forbidden base dependency" in error for error in errors))
        self.assertTrue(any("forbidden optional extra" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
