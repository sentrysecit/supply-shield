"""Unit tests for the npm packument to UpstreamMetadata mapping."""

from datetime import UTC, datetime

from supply_shield.core.enums import Ecosystem
from supply_shield.proxy.npm.packument import to_upstream_metadata

PACKUMENT = {
    "name": "express",
    "dist-tags": {"latest": "4.18.2"},
    "versions": {
        "4.17.1": {"version": "4.17.1"},
        "4.18.2": {
            "version": "4.18.2",
            "scripts": {"start": "node index.js", "postinstall": "node install.js"},
        },
    },
    "time": {
        "created": "2010-01-01T00:00:00.000Z",
        "4.17.1": "2019-05-10T00:00:00.000Z",
        "4.18.2": "2022-10-08T17:43:48.742Z",
    },
    "maintainers": [
        {"name": "dougwilson", "email": "doug@example.com"},
        {"name": "express-bot"},
    ],
}


def test_to_upstream_metadata_sets_package_ref() -> None:
    metadata = to_upstream_metadata(PACKUMENT)
    assert metadata.package.ecosystem == Ecosystem.NPM
    assert metadata.package.name == "express"


def test_to_upstream_metadata_extracts_versions_with_dates() -> None:
    metadata = to_upstream_metadata(PACKUMENT)
    versions = {r.version: r for r in metadata.versions}
    assert set(versions) == {"4.17.1", "4.18.2"}
    assert versions["4.18.2"].published_at == datetime(
        2022,
        10,
        8,
        17,
        43,
        48,
        742000,
        tzinfo=UTC,
    )
    assert versions["4.17.1"].published_at is not None


def test_to_upstream_metadata_extracts_scripts_from_latest() -> None:
    metadata = to_upstream_metadata(PACKUMENT)
    assert metadata.scripts == {"start": "node index.js", "postinstall": "node install.js"}


def test_to_upstream_metadata_extracts_maintainers() -> None:
    metadata = to_upstream_metadata(PACKUMENT)
    assert len(metadata.maintainers) == 2
    assert metadata.maintainers[0].name == "dougwilson"
    assert metadata.maintainers[0].email == "doug@example.com"
    assert metadata.maintainers[1].email is None


def test_to_upstream_metadata_handles_missing_fields() -> None:
    metadata = to_upstream_metadata({"name": "bare-pkg", "versions": {}})
    assert metadata.package.name == "bare-pkg"
    assert metadata.versions == ()
    assert metadata.scripts == {}
    assert metadata.maintainers == ()


def test_to_upstream_metadata_skips_invalid_time() -> None:
    packument = {
        "name": "pkg",
        "versions": {"1.0.0": {"version": "1.0.0"}},
        "time": {"1.0.0": "not-a-date"},
    }
    metadata = to_upstream_metadata(packument)
    assert metadata.versions[0].published_at is None


def test_to_upstream_metadata_ignores_fields_with_unexpected_types() -> None:
    packument = {
        "name": "odd-pkg",
        "versions": None,
        "time": "yesterday",
        "dist-tags": ["latest"],
        "maintainers": 42,
    }
    metadata = to_upstream_metadata(packument)
    assert metadata.package.name == "odd-pkg"
    assert metadata.versions == ()
    assert metadata.maintainers == ()
