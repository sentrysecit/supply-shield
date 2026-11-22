"""Bandas de severidad de los avisos de OSV."""

import pytest

from supply_shield.analyzers.vulnerabilities.severity import (
    score_to_severity,
    severity_from_advisory,
)
from supply_shield.core.enums import Severity

_CRITICAL = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
_HIGH = "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"
_V30 = "CVSS:3.0/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"
_ZERO = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N"
_V4 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:N"
_UNREADABLE = "CVSS:3.1/AV:N"


@pytest.mark.parametrize(
    ("score", "severity"),
    [
        (0.0, Severity.UNKNOWN),
        (0.1, Severity.LOW),
        (3.9, Severity.LOW),
        (4.0, Severity.MEDIUM),
        (6.9, Severity.MEDIUM),
        (7.0, Severity.HIGH),
        (8.9, Severity.HIGH),
        (9.0, Severity.CRITICAL),
        (10.0, Severity.CRITICAL),
    ],
)
def test_score_bands(score: float, severity: Severity) -> None:
    assert score_to_severity(score) is severity


def test_v31_vector_beats_ecosystem_label_and_v4() -> None:
    severity = severity_from_advisory(
        [("CVSS_V3", _HIGH), ("CVSS_V4", _V4)],
        "LOW",
    )
    assert severity is Severity.HIGH


def test_v30_is_used_when_v31_is_absent() -> None:
    assert severity_from_advisory([("CVSS_V3", _V30)], "LOW") is Severity.MEDIUM


def test_highest_v31_score_wins_when_several_are_present() -> None:
    severity = severity_from_advisory(
        [("CVSS_V3", _V30), ("CVSS_V3", _CRITICAL)],
        None,
    )
    assert severity is Severity.CRITICAL


def test_zero_base_score_is_unknown_and_does_not_use_the_ecosystem_label() -> None:
    assert severity_from_advisory([("CVSS_V3", _ZERO)], "CRITICAL") is Severity.UNKNOWN


def test_unreadable_v3_does_not_fall_through_to_v4() -> None:
    severity = severity_from_advisory(
        [("CVSS_V3", _UNREADABLE), ("CVSS_V4", _V4)],
        "HIGH",
    )
    assert severity is Severity.HIGH


def test_unreadable_v3_without_ecosystem_severity_is_unknown() -> None:
    assert severity_from_advisory([("CVSS_V3", _UNREADABLE)], None) is Severity.UNKNOWN


def test_v4_is_used_only_when_no_v3_vector_is_present() -> None:
    assert severity_from_advisory([("CVSS_V4", _V4)], "LOW") is Severity.CRITICAL


def test_moderate_ecosystem_severity_is_medium() -> None:
    assert severity_from_advisory([], "MODERATE") is Severity.MEDIUM


def test_missing_score_and_label_is_unknown() -> None:
    assert severity_from_advisory([], None) is Severity.UNKNOWN
    assert severity_from_advisory([], "not-a-severity") is Severity.UNKNOWN
