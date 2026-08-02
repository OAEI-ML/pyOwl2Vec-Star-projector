from __future__ import annotations

from pathlib import Path

from tools.check_dependency_dag import check_dependency_dag

ROOT = Path(__file__).resolve().parents[1]


def test_repository_projector_boundary_is_acyclic() -> None:
    assert check_dependency_dag(ROOT) == []


def test_oaei_optional_dependency_and_import_cycle_are_detected(tmp_path: Path) -> None:
    projector = tmp_path / "projector"
    projector_package = projector / "src" / "projector"
    projector_package.mkdir(parents=True)
    (projector / "pyproject.toml").write_text(
        '[project]\nname="projector"\ndependencies=["pyowl-core>=0.2,<0.3"]\n',
        encoding="utf-8",
    )
    (projector_package / "__init__.py").write_text("", encoding="utf-8")

    oaei = tmp_path / "oaei"
    oaei_package = oaei / "src" / "oaei_bioml_eval"
    oaei_package.mkdir(parents=True)
    (oaei / "pyproject.toml").write_text(
        """
[project]
name = "oaei-bioml-eval"
dependencies = []
[project.optional-dependencies]
coherence = ["pyowl2vec_star_projector>=0.1"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (oaei_package / "coherence.py").write_text(
        "import pyowl2vec_star_projector\n",
        encoding="utf-8",
    )
    errors = check_dependency_dag(projector, oaei)
    assert errors == [
        "OAEI: forbidden dependencies: pyowl2vec-star-projector",
        "OAEI: forbidden imports in src/oaei_bioml_eval/coherence.py: pyowl2vec_star_projector",
    ]


def test_projector_reverse_import_and_poetry_dependency_are_detected(tmp_path: Path) -> None:
    package = tmp_path / "src" / "projector"
    package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "projector"
dependencies = []
[tool.poetry.dependencies]
python = "^3.10"
Exact-OM = "2.1.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "bad.py").write_text("import exact\n", encoding="utf-8")
    assert check_dependency_dag(tmp_path) == [
        "projector: forbidden dependencies: exact-om",
        "projector: forbidden imports in src/projector/bad.py: exact",
    ]
