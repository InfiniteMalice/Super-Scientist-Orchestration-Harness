import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from super_scientist import __version__


def test_package_exposes_version() -> None:
    assert __version__ == "0.3.0"


def test_build_backend_range_preserves_twine_compatible_core_metadata() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    build_requirements = tuple(
        Requirement(requirement) for requirement in project["build-system"]["requires"]
    )
    hatchling = next(item for item in build_requirements if item.name == "hatchling")
    twine = Requirement(
        next(
            requirement
            for requirement in project["project"]["optional-dependencies"]["dev"]
            if Requirement(requirement).name == "twine"
        )
    )

    assert Version("1.31.0") in hatchling.specifier
    assert Version("1.32.0") not in hatchling.specifier
    assert Version("6.2.0") in twine.specifier
