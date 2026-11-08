"""Carga de la política desde YAML y su conexión con la aplicación."""

from pathlib import Path

import pytest

from supply_shield.config import Settings
from supply_shield.core import Severity
from supply_shield.main import create_app
from supply_shield.policy import (
    DEFAULT_RULE,
    DEFAULT_RULES,
    Policy,
    PolicyLoadError,
    Rule,
    RuleMode,
    load_policy,
    parse_policy,
)

_REPO_POLICY = Path(__file__).resolve().parents[3] / "config" / "policy.yaml"


def test_shipped_policy_matches_the_factory_policy() -> None:
    policy = load_policy(_REPO_POLICY)

    assert policy == Policy()
    assert policy.rules == DEFAULT_RULES
    assert policy.default_rule == DEFAULT_RULE


def test_empty_document_is_the_factory_policy() -> None:
    assert parse_policy("") == Policy()
    assert parse_policy("# only comments\n") == Policy()


def test_full_policy_is_parsed() -> None:
    policy = parse_policy(
        """
version: 1
rules:
  install_scripts:
    mode: block
projects:
  backend:
    rules:
      vulnerability:
        severity_threshold: high
exceptions:
  - package: {ecosystem: pypi, name: Requests, version: "2.19.0"}
    advisory: PYSEC-2018-28
    justification: Pinned by a vendor SDK; upgrade tracked in SEC-42
    author: sec-team
    expires: 2026-12-31
"""
    )

    assert policy.rules[Rule.INSTALL_SCRIPTS].mode is RuleMode.BLOCK
    assert policy.rule_for("vulnerability", "backend").severity_threshold is Severity.HIGH
    (exception,) = policy.exceptions
    assert exception.package is not None
    assert exception.package.name == "requests"
    assert exception.expires.isoformat() == "2026-12-31"


def test_invalid_yaml_raises_a_policy_error() -> None:
    with pytest.raises(PolicyLoadError, match="not valid YAML"):
        parse_policy("rules: [unclosed", source="broken.yaml")


@pytest.mark.parametrize(
    "text",
    [
        "- just\n- a list\n",
        "rules:\n  vulnerabilty:\n    mode: block\n",
        "rules:\n  vulnerability:\n    mode: deny\n",
        "exceptions:\n  - advisory: GHSA-1\n    author: me\n    expires: 2026-12-31\n",
        "exceptions:\n  - justification: x\n    author: me\n    expires: 2026-12-31\n",
    ],
)
def test_schema_violations_raise_a_policy_error(text: str) -> None:
    with pytest.raises(PolicyLoadError, match=r"policy bad\.yaml is invalid"):
        parse_policy(text, source="bad.yaml")


def test_load_policy_reports_the_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("version: 7\n", encoding="utf-8")

    with pytest.raises(PolicyLoadError, match=r"policy\.yaml"):
        load_policy(path)


def test_load_policy_propagates_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "missing.yaml")


def test_app_loads_the_configured_policy(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("rules:\n  new_package:\n    mode: log\n", encoding="utf-8")

    app = create_app(Settings(_env_file=None, policy_file=path))

    policy = app.state.policy
    assert isinstance(policy, Policy)
    assert policy.rules[Rule.NEW_PACKAGE].mode is RuleMode.LOG


def test_app_uses_the_factory_policy_when_the_file_is_missing(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, policy_file=tmp_path / "missing.yaml"))

    assert app.state.policy == Policy()


def test_app_refuses_to_start_with_an_invalid_policy(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("rules:\n  vulnerability:\n    mode: deny\n", encoding="utf-8")

    with pytest.raises(PolicyLoadError):
        create_app(Settings(_env_file=None, policy_file=path))
