"""Unit tests for converting the PyPI JSON API into ``UpstreamMetadata``."""

from datetime import UTC, datetime

from supply_shield.core import Ecosystem, Maintainer
from supply_shield.proxy.pypi.metadata import parse_pypi_metadata

PAYLOAD = {
    "info": {
        "name": "Django",
        "version": "5.0",
        "author": "Django Software Foundation",
        "author_email": "foundation@djangoproject.com",
    },
    "releases": {
        "5.0": [
            {"upload_time_iso_8601": "2023-12-04T10:00:00.000000Z"},
            {"upload_time_iso_8601": "2023-12-04T09:00:00.000000Z"},
        ],
        "4.2": [{"upload_time_iso_8601": "2023-04-03T12:00:00+00:00"}],
        "not@valid": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z"}],
    },
}


def test_package_is_normalized_and_uses_latest_version() -> None:
    metadata = parse_pypi_metadata("Django", PAYLOAD)
    assert metadata.package.ecosystem is Ecosystem.PYPI
    assert metadata.package.name == "django"
    assert metadata.package.version == "5.0"


def test_versions_use_earliest_upload_per_release() -> None:
    metadata = parse_pypi_metadata("django", PAYLOAD)
    by_version = {release.version: release.published_at for release in metadata.versions}
    assert by_version["5.0"] == datetime(2023, 12, 4, 9, 0, tzinfo=UTC)
    assert by_version["4.2"] == datetime(2023, 4, 3, 12, 0, tzinfo=UTC)
    assert "not@valid" not in by_version


def test_maintainers_fall_back_to_author() -> None:
    metadata = parse_pypi_metadata("django", PAYLOAD)
    assert metadata.maintainers == (
        Maintainer(name="Django Software Foundation", email="foundation@djangoproject.com"),
    )


def test_maintainer_takes_priority_when_present() -> None:
    payload = {
        "info": {
            "version": "1.0",
            "author": "Original",
            "maintainer": "Current",
            "maintainer_email": "current@example.com",
        },
        "releases": {"1.0": []},
    }
    metadata = parse_pypi_metadata("pkg", payload)
    assert metadata.maintainers == (Maintainer(name="Current", email="current@example.com"),)


def test_missing_author_yields_no_maintainers() -> None:
    metadata = parse_pypi_metadata("pkg", {"info": {"version": "1.0"}, "releases": {}})
    assert metadata.maintainers == ()
    assert metadata.versions == ()


def test_tolerates_garbage_payload() -> None:
    metadata = parse_pypi_metadata("pkg", "not-a-mapping")
    assert metadata.versions == ()
    assert metadata.maintainers == ()
    assert metadata.package.name == "pkg"
