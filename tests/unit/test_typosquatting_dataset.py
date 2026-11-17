"""Armado del dataset de medición del typosquatting (D4-6), sin red."""

import base64
import csv
import hashlib
import io
import json
import tarfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

import build_dataset
from build_dataset import (
    Advisory,
    DatasetError,
    Downloads,
    EcosystemSources,
    Provenance,
    TyposquatRow,
    build_groups,
    download,
    download_npm_tarball,
    parse_datadog_manifest,
    parse_npm_ranking,
    parse_pypi_ranking,
    parse_pypi_simple_index,
    read_labels,
    read_ossf_advisories,
    sample_names,
)
from fetch_popular import NPM_LIST_MEMBER, NPM_REGISTRY, FetchError
from supply_shield.analyzers.typosquatting import PopularPackages, PopularPackagesSource
from supply_shield.core.enums import Ecosystem

NPM = Ecosystem.NPM
PYPI = Ecosystem.PYPI
ROOT = "malicious-packages-0123abc"


def _tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _osv(advisory_id: str, ecosystem: str, name: str, details: str | None = None) -> bytes:
    entry = {
        "id": advisory_id,
        "summary": f"Malicious code in {name}",
        "details": details,
        "affected": [{"package": {"ecosystem": ecosystem, "name": name}}],
    }
    return json.dumps(entry).encode()


def _labels(path: Path, rows: Sequence[Sequence[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["ecosystem", "name", "target"])
        writer.writerows(rows)
    return path


def _popular(directory: Path, ecosystem: Ecosystem, names: Sequence[str]) -> None:
    moment = datetime(2026, 11, 1, tzinfo=UTC)
    popular = PopularPackages(
        ecosystem=ecosystem,
        generated_at=moment,
        source=PopularPackagesSource(
            name="test", url="https://example.invalid", ranking="test", published_at=moment
        ),
        packages=tuple(names),
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{ecosystem.value}.json").write_text(popular.model_dump_json(), encoding="utf-8")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_reads_only_malicious_npm_and_pypi_advisories() -> None:
    archive = _tarball(
        {
            f"{ROOT}/README.md": b"# malicious-packages",
            f"{ROOT}/osv/malicious/npm/crossenv/MAL-1.json": _osv(
                "MAL-1", "npm", "crossenv", "crossenv is a typosquat of cross-env"
            ),
            f"{ROOT}/osv/malicious/npm/@evil/x/MAL-2.json": _osv("MAL-2", "npm", "@Evil/X"),
            f"{ROOT}/osv/malicious/pypi/py-reqs/MAL-3.json": _osv(
                "MAL-3", "PyPI", "Py_Reqs", "Typo-squatting campaign"
            ),
            f"{ROOT}/osv/malicious/npm/bad/MAL-4.json": _osv("MAL-4", "npm", "not valid!"),
            f"{ROOT}/osv/malicious/npm/gem/MAL-5.json": _osv("MAL-5", "RubyGems", "gem"),
            f"{ROOT}/osv/malicious/rubygems/gem/MAL-6.json": _osv("MAL-6", "RubyGems", "gem"),
            f"{ROOT}/osv/withdrawn/npm/old/MAL-7.json": _osv("MAL-7", "npm", "old", "typosquat"),
        }
    )

    advisories = list(read_ossf_advisories(archive))

    assert advisories == [
        Advisory("MAL-1", NPM, ("crossenv",), declares_typosquat=True),
        Advisory("MAL-2", NPM, ("@evil/x",), declares_typosquat=False),
        Advisory("MAL-3", PYPI, ("py-reqs",), declares_typosquat=True),
    ]


def test_corrupt_advisory_archive_is_an_error() -> None:
    with pytest.raises(DatasetError, match="cannot be read"):
        list(read_ossf_advisories(b"not a tarball"))


def test_datadog_manifest_names_are_normalized_and_invalid_ones_dropped() -> None:
    content = json.dumps({"Roblox.-com": None, "bad name!": None, "colorama": ["0.4.7"]})

    assert parse_datadog_manifest(PYPI, content.encode()) == {"roblox-com", "colorama"}


def test_rankings_and_registry_lists_are_parsed_in_order() -> None:
    pypi = {
        "rows": [
            {"project": "requests", "download_count": 5},
            {"project": "Boto3", "download_count": 9},
        ]
    }
    npm = _tarball({NPM_LIST_MEMBER: b"export const topDownload = ['semver', 'React']\n"})
    simple = {"meta": {"api-version": "1.1"}, "projects": [{"name": "Django"}, {"name": "x"}]}

    assert parse_pypi_ranking(json.dumps(pypi).encode()) == ("boto3", "requests")
    assert parse_npm_ranking(npm) == ("semver", "react")
    assert parse_pypi_simple_index(json.dumps(simple).encode()) == ["Django", "x"]


def test_labels_are_read_with_an_empty_target_meaning_none(tmp_path: Path) -> None:
    path = _labels(
        tmp_path / "labels.csv",
        [["npm", "crossenv", "cross-env"], ["pypi", "adanbu", ""], ["npm", "@x/ws", "ws"]],
    )

    assert read_labels(path) == {
        (NPM, "crossenv"): "cross-env",
        (PYPI, "adanbu"): None,
        (NPM, "@x/ws"): "ws",
    }


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([["npm", "Crossenv", ""]], r"labels.csv:2: 'Crossenv' is not normalized"),
        ([["pypi", "reqursts", "Requests"]], r"labels.csv:2: 'Requests' is not normalized"),
        ([["maven", "x", ""]], r"labels.csv:2: 'maven' is not a valid Ecosystem"),
        ([["npm", "a", ""], ["npm", "a", "b"]], r"labels.csv:3: npm name 'a' is labelled twice"),
        ([["npm", "a"]], r"labels.csv:2: expected 3 columns"),
    ],
)
def test_invalid_labels_fail_with_the_line(
    tmp_path: Path, rows: list[list[str]], message: str
) -> None:
    path = _labels(tmp_path / "labels.csv", rows)

    with pytest.raises(DatasetError, match=message):
        read_labels(path)


def test_labels_need_the_header(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("npm,crossenv,cross-env\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="expected header"):
        read_labels(path)


def test_sample_is_reproducible_order_independent_and_stable() -> None:
    names = [f"pkg-{i}" for i in range(1000)]

    sample = sample_names(names, 50, seed="seed")
    grown = sample_names([*reversed(names), *(f"new-{i}" for i in range(100))], 50, seed="seed")

    assert sample == sorted(sample)
    assert len(sample) == 50
    assert sample == sample_names(reversed(names), 50, seed="seed")
    assert sample != sample_names(names, 50, seed="other")
    # Con nombres nuevos, los viejos que siguen en la muestra ya estaban en ella.
    assert {name for name in grown if name.startswith("pkg-")} <= set(sample)


def test_groups_keep_known_names_out_of_the_negatives() -> None:
    sources = EcosystemSources(
        popular=frozenset({"react", "lodash"}),
        malicious=frozenset({"react", "evil", "reakt", "unlabelled"}),
        declared={"reakt": ("MAL-1",), "unlabelled": ("MAL-2",)},
        ranking=("react", "lodash", "next-a", "evil", "next-b"),
        registry=["react", "next-a", "evil", "rand-1", "reakt", "rand-2", "next-b"],
    )

    dataset = build_groups(NPM, sources, {(NPM, "reakt"): "react"}, sample_size=10)

    assert dataset.typosquats == [TyposquatRow("reakt", "react", ("MAL-1",))]
    assert dataset.unreviewed == [TyposquatRow("unlabelled", None, ("MAL-2",))]
    assert dataset.next == [(3, "next-a"), (5, "next-b")]
    assert dataset.random == ["rand-1", "rand-2"]
    assert dataset.overlaps == {
        "malicious_in_popular_list": ["react"],
        "malicious_in_next_ranking": ["evil"],
        "declared_typosquats_in_popular_list": [],
        "declared_typosquats_in_next_ranking": [],
    }


async def test_download_stops_at_the_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_dataset, "MAX_DOWNLOAD_BYTES", 10)
    with respx.mock() as router:
        router.get("https://example.invalid/big").respond(200, content=b"x" * 100)
        async with httpx.AsyncClient() as client:
            with pytest.raises(DatasetError, match="larger than 10 bytes"):
                await download(client, "https://example.invalid/big")


async def test_download_keeps_the_body_and_its_sha256() -> None:
    with respx.mock() as router:
        router.get("https://example.invalid/data").respond(200, content=b"data")
        async with httpx.AsyncClient() as client:
            result = await download(client, "https://example.invalid/data")

    assert result.content == b"data"
    assert result.sha256 == hashlib.sha256(b"data").hexdigest()


async def test_npm_tarball_must_come_from_the_registry_and_match_its_integrity() -> None:
    tarball = b"tarball bytes"
    good = f"{NPM_REGISTRY}/pkg/-/pkg-1.0.0.tgz"
    with respx.mock() as router:
        route = router.get(f"{NPM_REGISTRY}/pkg/1.0.0")
        router.get(good).respond(200, content=tarball)
        async with httpx.AsyncClient() as client:
            route.respond(200, json={"dist": {"tarball": good, "integrity": _integrity(tarball)}})
            assert (await download_npm_tarball(client, "pkg", "1.0.0")).content == tarball

            route.respond(200, json={"dist": {"tarball": good, "integrity": _integrity(b"x")}})
            with pytest.raises(FetchError, match="does not match"):
                await download_npm_tarball(client, "pkg", "1.0.0")

            outside = "https://evil.example/pkg.tgz"
            route.respond(200, json={"dist": {"tarball": outside, "integrity": "sha512-x"}})
            with pytest.raises(DatasetError, match="not served by"):
                await download_npm_tarball(client, "pkg", "1.0.0")


def _downloads() -> Downloads:
    return Downloads(
        advisories=[
            Advisory("MAL-1", NPM, ("crossenv",), declares_typosquat=True),
            Advisory("MAL-2", PYPI, ("reqursts",), declares_typosquat=True),
            Advisory("MAL-3", NPM, ("evil-pkg",), declares_typosquat=False),
            Advisory("MAL-4", NPM, ("new-squat",), declares_typosquat=True),
        ],
        datadog={NPM: frozenset({"dd-bad"}), PYPI: frozenset()},
        rankings={
            NPM: ("cross-env", "lodash", "left-pad", "dd-bad"),
            PYPI: ("requests", "flask", "django"),
        },
        registry={
            NPM: ["cross-env", "left-pad", "random-a", "Random-B", "evil-pkg", "not valid!"],
            PYPI: ["requests", "Random_C"],
        },
        provenance=[Provenance("test", "https://example.invalid", "abc", "00", "2026-11-13")],
    )


def test_main_writes_the_dataset_its_sources_and_the_pending_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch() -> Downloads:
        return _downloads()

    monkeypatch.setattr(build_dataset, "fetch_sources", fake_fetch)
    popular_dir = tmp_path / "popular"
    _popular(popular_dir, NPM, ["cross-env", "lodash"])
    _popular(popular_dir, PYPI, ["requests", "flask"])
    labels = _labels(
        tmp_path / "labels.csv", [["npm", "crossenv", "cross-env"], ["pypi", "reqursts", ""]]
    )
    out = tmp_path / "dataset"
    pending = tmp_path / "pending.csv"

    code = build_dataset.main(
        [
            *("--output-dir", str(out), "--labels", str(labels)),
            *("--popular-dir", str(popular_dir), "--sample-size", "10"),
            *("--pending", str(pending)),
        ]
    )

    assert code == 0
    assert _rows(out / "npm-typosquats.csv") == [
        {"name": "crossenv", "target": "cross-env", "advisories": "MAL-1"}
    ]
    assert _rows(out / "pypi-typosquats.csv") == [
        {"name": "reqursts", "target": "", "advisories": "MAL-2"}
    ]
    assert _rows(out / "npm-next.csv") == [{"rank": "3", "name": "left-pad"}]
    assert _rows(out / "pypi-next.csv") == [{"rank": "3", "name": "django"}]
    assert _rows(out / "npm-random.csv") == [{"name": "random-a"}, {"name": "random-b"}]
    assert _rows(out / "pypi-random.csv") == [{"name": "random-c"}]
    sources = json.loads((out / "sources.json").read_text(encoding="utf-8"))
    assert sources["groups"]["npm"] == {
        "typosquat": 1,
        "typosquat_untargeted": 0,
        "typosquat_unreviewed": 1,
        "next": 1,
        "random": 2,
    }
    assert sources["groups"]["pypi"]["typosquat_untargeted"] == 1
    assert sources["sources"][0]["revision"] == "abc"
    assert _rows(pending) == [{"ecosystem": "npm", "name": "new-squat", "target": ""}]
    assert (out / "npm-next.csv").read_bytes().endswith(b"left-pad\n")


def test_main_fails_without_the_labels_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch() -> Downloads:
        raise AssertionError  # No debe descargar nada si faltan las etiquetas.

    monkeypatch.setattr(build_dataset, "fetch_sources", fake_fetch)

    code = build_dataset.main(
        ["--labels", str(tmp_path / "missing.csv"), "--output-dir", str(tmp_path / "out")]
    )

    assert code == 1
    assert not (tmp_path / "out").exists()


def test_sample_size_must_be_positive() -> None:
    with pytest.raises(SystemExit):
        build_dataset.parse_args(["--sample-size", "0"])
