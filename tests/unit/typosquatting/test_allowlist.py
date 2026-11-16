"""Lista de nombres permitidos del detector de typosquatting."""

from pathlib import Path

import pytest

from supply_shield.analyzers.typosquatting import (
    AllowlistLoadError,
    TyposquattingAllowlist,
    load_allowlist,
    parse_allowlist,
)
from supply_shield.core import Ecosystem

_REPO_ALLOWLIST = Path(__file__).resolve().parents[3] / "data" / "typosquatting" / "allowlist.yaml"


def test_shipped_allowlist_loads() -> None:
    assert isinstance(load_allowlist(_REPO_ALLOWLIST), TyposquattingAllowlist)


def test_entries_are_split_by_ecosystem() -> None:
    allowlist = parse_allowlist(
        """
version: 1
npm:
  - name: "@acme/utils"
    reason: Internal fork, reviewed by security
pypi:
  - name: pyaml
    reason: Legit wrapper around PyYAML
"""
    )

    assert allowlist.names(Ecosystem.NPM) == frozenset({"@acme/utils"})
    assert allowlist.names(Ecosystem.PYPI) == frozenset({"pyaml"})
    (entry,) = allowlist.entries(Ecosystem.PYPI)
    assert entry.reason == "Legit wrapper around PyYAML"


def test_an_empty_document_allows_nothing() -> None:
    allowlist = parse_allowlist("# nothing yet\n")

    assert allowlist.names(Ecosystem.NPM) == frozenset()
    assert allowlist.names(Ecosystem.PYPI) == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "pypi:\n  - name: pyaml\n",
        "pypi:\n  - name: pyaml\n    reason: '   '\n",
        "pypi:\n  - name: pyaml\n    reason: ok\n    extra: field\n",
        "version: 2\n",
        "maven: []\n",
        "- just a list\n",
    ],
)
def test_schema_violations_are_rejected(text: str) -> None:
    with pytest.raises(AllowlistLoadError, match=r"allowlist bad\.yaml is invalid"):
        parse_allowlist(text, source="bad.yaml")


def test_names_must_be_normalized() -> None:
    with pytest.raises(AllowlistLoadError, match="write 'django-foo'"):
        parse_allowlist("pypi:\n  - name: Django_Foo\n    reason: ok\n")


def test_invalid_names_are_rejected() -> None:
    with pytest.raises(AllowlistLoadError, match="invalid npm package name"):
        parse_allowlist("npm:\n  - name: 'bad name'\n    reason: ok\n")


def test_invalid_yaml_is_rejected() -> None:
    with pytest.raises(AllowlistLoadError, match="not valid YAML"):
        parse_allowlist("npm: [unclosed")


def test_load_reports_the_path_and_propagates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.yaml"
    path.write_text("version: 9\n", encoding="utf-8")

    with pytest.raises(AllowlistLoadError, match=r"allowlist\.yaml"):
        load_allowlist(path)
    with pytest.raises(FileNotFoundError):
        load_allowlist(tmp_path / "missing.yaml")
