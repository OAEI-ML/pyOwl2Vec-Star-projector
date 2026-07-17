from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

_HAS_TOML = (
    importlib.util.find_spec("tomllib") is not None or importlib.util.find_spec("tomli") is not None
)


@unittest.skipUnless(_HAS_TOML, "tomllib/tomli is a declared development dependency")
class BaselineTests(unittest.TestCase):
    def test_checked_in_baseline(self) -> None:
        from tools.check_baseline import validate_baseline

        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_baseline(root / "specs" / "baseline.toml"), [])

    def test_optional_blob_validation(self) -> None:
        from tools.check_baseline import validate_baseline

        source = b"object Example {}\n"
        digest = hashlib.sha1(
            f"blob {len(source)}\0".encode() + source,
            usedforsecurity=False,
        ).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / "source.scala"
            source_file.write_bytes(source)
            baseline = root / "baseline.toml"
            baseline.write_text(
                f'''schema = "pyowl-projector.reference-baseline/1"
profile = "mowl-d993536-v1"
[upstream]
commit = "{"a" * 40}"
source_path = "source.scala"
source_blob = "{digest}"
immutable_source_url = "https://example/{"a" * 40}/source.scala"
license = "BSD-3-Clause"
[compatibility]
output_semantics = "bag"
canonical_order = "utf8(source,relation,destination)"
''',
                encoding="utf-8",
            )
            self.assertEqual(validate_baseline(baseline, source_file), [])
            source_file.write_bytes(b"changed")
            self.assertTrue(validate_baseline(baseline, source_file))


if __name__ == "__main__":
    unittest.main()
