"""Criterios de aceptación de D4-4 con las listas populares reales del repo (sin red)."""

import gc
import statistics
import time
from pathlib import Path

import pytest
from rapidfuzz.distance import DamerauLevenshtein

from supply_shield.analyzers.typosquatting import SeverityThresholds, TyposquattingAnalyzer
from supply_shield.analyzers.typosquatting.scoring import _candidates
from supply_shield.core import DecisionAction, Ecosystem, PackageRef, RequestContext, Severity
from supply_shield.policy import Policy, evaluate

_ROOT = Path(__file__).resolve().parents[3]
_CONTEXT = RequestContext(project="frontend")
_P95_BUDGET_MS = 5.0
_TOP = 1000


@pytest.fixture(scope="module")
def analyzer() -> TyposquattingAnalyzer:
    return TyposquattingAnalyzer.from_files(
        _ROOT / "data" / "popular", _ROOT / "data" / "typosquatting" / "allowlist.yaml"
    )


@pytest.mark.parametrize(
    ("ecosystem", "name", "target"),
    [
        (Ecosystem.NPM, "crossenv", "cross-env"),
        (Ecosystem.PYPI, "python3-dateutil", "python-dateutil"),
        (Ecosystem.PYPI, "jeIlyfish", "jellyfish"),
        (Ecosystem.NPM, "loadsh", "lodash"),
        (Ecosystem.NPM, "@typse/node", "@types/node"),
        (Ecosystem.PYPI, "djnago", "django"),
        (Ecosystem.PYPI, "urlib3", "urllib3"),
    ],
)
async def test_known_typosquats_are_blocked(
    analyzer: TyposquattingAnalyzer, ecosystem: Ecosystem, name: str, target: str
) -> None:
    package = PackageRef.of(ecosystem, name, "1.0.0")

    findings = await analyzer.analyze(package, _CONTEXT)

    (finding,) = findings
    assert f"'{target}'" in finding.detail
    assert finding.severity in {Severity.HIGH, Severity.CRITICAL}
    assert evaluate(findings, _CONTEXT, Policy()).action is DecisionAction.BLOCK


@pytest.mark.parametrize("ecosystem", list(Ecosystem))
def test_no_package_of_the_top_1000_is_flagged(
    analyzer: TyposquattingAnalyzer, ecosystem: Ecosystem
) -> None:
    flagged = [
        name
        for name in analyzer.indexes[ecosystem].names[:_TOP]
        if analyzer.match(PackageRef.of(ecosystem, name)) is not None
    ]

    assert flagged == []


@pytest.mark.parametrize("ecosystem", list(Ecosystem))
def test_prefilter_never_misses_a_damerau_levenshtein_neighbour(
    analyzer: TyposquattingAnalyzer, ecosystem: Ecosystem
) -> None:
    index = analyzer.indexes[ecosystem]
    weights = analyzer.weights
    probes = [name[::-1][:3] + name for name in index.names[:150:3]]
    probes += [name[1:] + "x" for name in index.names[:150:3] if len(name) > 4]

    for probe in probes:
        expected = {name for name in index.names if DamerauLevenshtein.distance(probe, name) <= 2}
        assert expected <= _candidates(probe, index, weights, frozenset()), probe


def test_p95_latency_is_under_budget(analyzer: TyposquattingAnalyzer) -> None:
    probes: list[PackageRef] = []
    for ecosystem in Ecosystem:
        names = analyzer.indexes[ecosystem].names
        probes += [PackageRef.of(ecosystem, name + "x") for name in names[:250]]
        probes += [
            PackageRef.of(ecosystem, "py-" + name)
            for name in names[250:400]
            if not name.startswith("@")
        ]
        probes += [PackageRef.of(ecosystem, f"unrelated-name-{i}") for i in range(100)]
    for package in probes[:50]:
        analyzer.match(package)

    timings: list[float] = []
    gc.disable()
    try:
        for package in probes:
            started = time.perf_counter()
            analyzer.match(package)
            timings.append((time.perf_counter() - started) * 1000)
    finally:
        gc.enable()

    p95 = statistics.quantiles(timings, n=100)[94]
    assert p95 < _P95_BUDGET_MS, f"p95 = {p95:.2f} ms over {len(timings)} names"


def test_default_thresholds_are_the_documented_ones(analyzer: TyposquattingAnalyzer) -> None:
    assert analyzer.thresholds == SeverityThresholds(
        report=0.60, medium=0.70, high=0.85, critical=0.95
    )
