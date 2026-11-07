"""Carga del detector de typosquatting al arrancar la aplicación."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from supply_shield.analyzers.typosquatting import AllowlistLoadError, TyposquattingAnalyzer
from supply_shield.config import Settings
from supply_shield.main import create_app

_POPULAR = Path(__file__).resolve().parents[3] / "data" / "popular"


def test_app_loads_the_detector_with_the_configured_thresholds() -> None:
    app = create_app(Settings(_env_file=None, typosquatting_high_threshold=0.9))

    analyzer = app.state.typosquatting_analyzer
    assert isinstance(analyzer, TyposquattingAnalyzer)
    assert analyzer.thresholds.high == 0.9


def test_app_refuses_to_start_without_the_popular_lists(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        create_app(Settings(_env_file=None, popular_packages_dir=tmp_path))


def test_app_refuses_to_start_with_an_invalid_list(tmp_path: Path) -> None:
    shutil.copy(_POPULAR / "npm.json", tmp_path / "npm.json")
    (tmp_path / "pypi.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError):
        create_app(Settings(_env_file=None, popular_packages_dir=tmp_path))


def test_app_refuses_to_start_without_the_allowlist(tmp_path: Path) -> None:
    missing = tmp_path / "allowlist.yaml"

    with pytest.raises(FileNotFoundError):
        create_app(Settings(_env_file=None, typosquatting_allowlist_file=missing))


def test_app_refuses_to_start_with_an_invalid_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.yaml"
    path.write_text("npm:\n  - name: Crossenv\n    reason: x\n", encoding="utf-8")

    with pytest.raises(AllowlistLoadError):
        create_app(Settings(_env_file=None, typosquatting_allowlist_file=path))


def test_settings_reject_thresholds_out_of_order() -> None:
    with pytest.raises(ValidationError, match="report <= medium <= high <= critical"):
        Settings(_env_file=None, typosquatting_medium_threshold=0.9)
