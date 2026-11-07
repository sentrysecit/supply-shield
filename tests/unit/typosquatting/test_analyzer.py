"""Detector de typosquatting: del puntaje al hallazgo y a la decisión de la política."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from supply_shield.analyzers.typosquatting import (
    PopularIndex,
    SeverityThresholds,
    TyposquattingAllowlist,
    TyposquattingAnalyzer,
    parse_allowlist,
)
from supply_shield.core import (
    Analyzer,
    DecisionAction,
    Ecosystem,
    PackageRef,
    RequestContext,
    Severity,
)
from supply_shield.policy import Policy, evaluate

_CONTEXT = RequestContext(project="frontend")


@pytest.fixture
def analyzer(npm_index: PopularIndex, pypi_index: PopularIndex) -> TyposquattingAnalyzer:
    return TyposquattingAnalyzer(
        {Ecosystem.NPM: npm_index, Ecosystem.PYPI: pypi_index}, TyposquattingAllowlist()
    )


# --- Umbrales de severidad ---


@pytest.mark.parametrize(
    ("score", "severity"),
    [
        (1.0, Severity.CRITICAL),
        (0.95, Severity.CRITICAL),
        (0.9499, Severity.HIGH),
        (0.85, Severity.HIGH),
        (0.8, Severity.MEDIUM),
        (0.7, Severity.MEDIUM),
        (0.65, Severity.LOW),
        (0.6, Severity.LOW),
        (0.59, None),
    ],
)
def test_severity_for(score: float, severity: Severity | None) -> None:
    assert SeverityThresholds().severity_for(score) is severity


def test_thresholds_must_ascend() -> None:
    with pytest.raises(ValidationError, match="report <= medium <= high <= critical"):
        SeverityThresholds(high=0.99, critical=0.9)


# --- Construcción ---


def test_both_ecosystems_are_required(npm_index: PopularIndex) -> None:
    with pytest.raises(ValueError, match="missing popular package index for: pypi"):
        TyposquattingAnalyzer({Ecosystem.NPM: npm_index}, TyposquattingAllowlist())


def test_indexes_must_match_their_ecosystem(npm_index: PopularIndex) -> None:
    with pytest.raises(ValueError, match="wrong ecosystem"):
        TyposquattingAnalyzer(
            {Ecosystem.NPM: npm_index, Ecosystem.PYPI: npm_index}, TyposquattingAllowlist()
        )


def test_analyzer_implements_the_shared_protocol(analyzer: TyposquattingAnalyzer) -> None:
    detector: Analyzer = analyzer
    assert detector is analyzer


# --- Hallazgos ---


async def test_typosquat_produces_a_readable_finding(analyzer: TyposquattingAnalyzer) -> None:
    package = PackageRef.of(Ecosystem.NPM, "crossenv", "7.0.3")

    (finding,) = await analyzer.analyze(package, _CONTEXT)

    assert finding.rule == "typosquatting"
    assert finding.source == "typosquatting"
    assert finding.severity is Severity.CRITICAL
    assert finding.package == package
    assert finding.detail == (
        "El nombre 'crossenv' se parece al paquete popular 'cross-env' (#5 en npm); "
        "puntaje 1.00 por separators, edit_distance, jaro_winkler"
    )
    assert finding.references == ("https://www.npmjs.com/package/cross-env",)
    assert finding.advisory_ids == ()


async def test_pypi_findings_link_to_pypi(analyzer: TyposquattingAnalyzer) -> None:
    package = PackageRef.of(Ecosystem.PYPI, "jeIlyfish", "0.5.6")

    (finding,) = await analyzer.analyze(package, _CONTEXT)

    assert finding.references == ("https://pypi.org/project/jellyfish/",)
    assert "'jeilyfish'" in finding.detail


async def test_clean_and_popular_names_have_no_findings(analyzer: TyposquattingAnalyzer) -> None:
    assert await analyzer.analyze(PackageRef.of(Ecosystem.NPM, "left-pad"), _CONTEXT) == []
    assert await analyzer.analyze(PackageRef.of(Ecosystem.NPM, "lodash"), _CONTEXT) == []


async def test_matches_below_the_report_threshold_are_dropped(
    npm_index: PopularIndex, pypi_index: PopularIndex
) -> None:
    analyzer = TyposquattingAnalyzer(
        {Ecosystem.NPM: npm_index, Ecosystem.PYPI: pypi_index},
        TyposquattingAllowlist(),
        thresholds=SeverityThresholds(report=0.9, medium=0.9, high=0.9),
    )
    package = PackageRef.of(Ecosystem.PYPI, "py-requests")

    assert analyzer.match(package) is not None
    assert await analyzer.analyze(package, _CONTEXT) == []


async def test_allowlisted_names_are_not_flagged(
    npm_index: PopularIndex, pypi_index: PopularIndex
) -> None:
    allowlist = parse_allowlist("npm:\n  - name: crossenv\n    reason: test\n")
    analyzer = TyposquattingAnalyzer(
        {Ecosystem.NPM: npm_index, Ecosystem.PYPI: pypi_index}, allowlist
    )

    assert await analyzer.analyze(PackageRef.of(Ecosystem.NPM, "crossenv"), _CONTEXT) == []


async def test_the_factory_policy_blocks_strong_matches_and_logs_weak_ones(
    analyzer: TyposquattingAnalyzer,
) -> None:
    strong = await analyzer.analyze(PackageRef.of(Ecosystem.NPM, "loadsh", "4.17.21"), _CONTEXT)
    weak = await analyzer.analyze(PackageRef.of(Ecosystem.PYPI, "py-requests", "1.0"), _CONTEXT)

    assert evaluate(strong, _CONTEXT, Policy()).action is DecisionAction.BLOCK
    assert weak[0].severity is Severity.MEDIUM
    assert evaluate(weak, _CONTEXT, Policy()).action is DecisionAction.ALLOW


# --- Carga desde archivos ---


def _write_popular(directory: Path, ecosystem: str, names: list[str]) -> None:
    document = {
        "format_version": 1,
        "ecosystem": ecosystem,
        "generated_at": "2026-10-01T00:00:00Z",
        "source": {
            "name": "example",
            "url": "https://example.test/top.json",
            "ranking": "downloads",
            "published_at": "2026-10-01T00:00:00Z",
        },
        "packages": names,
    }
    (directory / f"{ecosystem}.json").write_text(json.dumps(document), encoding="utf-8")


def test_from_files_loads_both_lists_and_the_allowlist(tmp_path: Path) -> None:
    _write_popular(tmp_path, "npm", ["lodash"])
    _write_popular(tmp_path, "pypi", ["requests"])
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("pypi:\n  - name: reqests\n    reason: test\n", encoding="utf-8")

    analyzer = TyposquattingAnalyzer.from_files(
        tmp_path, allowlist, thresholds=SeverityThresholds(high=0.9)
    )

    assert len(analyzer.indexes[Ecosystem.NPM]) == 1
    assert analyzer.thresholds.high == 0.9
    assert analyzer.match(PackageRef.of(Ecosystem.PYPI, "reqests")) is None
    assert analyzer.match(PackageRef.of(Ecosystem.NPM, "loadsh")) is not None


def test_from_files_fails_without_a_list(tmp_path: Path) -> None:
    _write_popular(tmp_path, "npm", ["lodash"])
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        TyposquattingAnalyzer.from_files(tmp_path, allowlist)


def test_from_files_fails_with_a_list_under_the_wrong_name(tmp_path: Path) -> None:
    _write_popular(tmp_path, "npm", ["lodash"])
    (tmp_path / "pypi.json").write_bytes((tmp_path / "npm.json").read_bytes())
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="wrong ecosystem"):
        TyposquattingAnalyzer.from_files(tmp_path, allowlist)
