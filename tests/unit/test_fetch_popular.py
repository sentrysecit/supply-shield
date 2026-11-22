"""Script de descarga de paquetes populares, con todas las llamadas upstream simuladas."""

import base64
import hashlib
import io
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

import fetch_popular
from fetch_popular import (
    NPM_LIST_MEMBER,
    NPM_REGISTRY,
    PYPI_TOP_URL,
    FetchError,
    parse_args,
    parse_js_string_array,
    parse_naive_utc,
    read_tar_member,
    verify_integrity,
)
from supply_shield.analyzers.typosquatting.popular import load_popular_packages

PACKUMENT_URL = f"{NPM_REGISTRY}/npm-high-impact"
TARBALL_URL = f"{NPM_REGISTRY}/npm-high-impact/-/npm-high-impact-9.9.9.tgz"

# Orden de la fuente, con mayúsculas, duplicados y un nombre inválido.
NPM_NAMES = ["semver", "@Types/Node", "semver", "not valid!", "react", "lodash"]
PYPI_DUMP = {
    "last_update": "2026-10-01 12:40:51",
    "rows": [
        {"project": "Django_REST.framework", "download_count": 10},
        {"project": "requests", "download_count": 30},
        {"project": "REQUESTS", "download_count": 5},
        {"project": "bad name!", "download_count": 20},
        {"project": "boto3", "download_count": 40},
    ],
}


def _tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _js_list(names: Sequence[str]) -> bytes:
    body = ",\n".join(f"  '{name}'" for name in names)
    return f"export const topDownload = [\n{body}\n]\n".encode()


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _packument(tarball: bytes, *, url: str = TARBALL_URL, latest: bool = True) -> dict[str, object]:
    return {
        "name": "npm-high-impact",
        "dist-tags": {"latest": "9.9.9"} if latest else {},
        "versions": {"9.9.9": {"dist": {"tarball": url, "integrity": _integrity(tarball)}}},
        "time": {"created": "2022-01-01T00:00:00.000Z", "9.9.9": "2026-06-08T16:53:09.411Z"},
    }


NPM_TARBALL = _tarball({NPM_LIST_MEMBER: _js_list(NPM_NAMES)})


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    """Las dos fuentes responden con listas pequeñas y válidas."""
    with respx.mock(assert_all_called=False) as router:
        router.get(PYPI_TOP_URL, name="pypi").mock(return_value=httpx.Response(200, json=PYPI_DUMP))
        router.get(PACKUMENT_URL, name="packument").mock(
            return_value=httpx.Response(200, json=_packument(NPM_TARBALL))
        )
        router.get(TARBALL_URL, name="tarball").mock(
            return_value=httpx.Response(200, content=NPM_TARBALL)
        )
        yield router


def _run(output_dir: Path, *extra: str, limit: int = 3) -> int:
    return fetch_popular.main(["--limit", str(limit), "--output-dir", str(output_dir), *extra])


def test_writes_normalized_ranked_lists_with_date_and_source(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    assert _run(tmp_path) == 0

    pypi = load_popular_packages(tmp_path / "pypi.json")
    assert pypi.packages == ("boto3", "requests", "django-rest-framework")
    assert pypi.source.url == PYPI_TOP_URL
    assert pypi.source.published_at == datetime(2026, 10, 1, 12, 40, 51, tzinfo=UTC)

    npm = load_popular_packages(tmp_path / "npm.json")
    assert npm.packages == ("semver", "@types/node", "react")
    assert npm.source.name == "npm-high-impact@9.9.9 (topDownload)"
    assert npm.source.url == TARBALL_URL
    assert npm.source.published_at == datetime(2026, 6, 8, 16, 53, 9, 411000, tzinfo=UTC)
    assert npm.generated_at == pypi.generated_at


def test_files_use_lf_and_end_with_a_newline(upstream: respx.MockRouter, tmp_path: Path) -> None:
    assert _run(tmp_path) == 0

    raw = (tmp_path / "npm.json").read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r\n" not in raw


def test_same_ranking_leaves_the_file_untouched(upstream: respx.MockRouter, tmp_path: Path) -> None:
    assert _run(tmp_path) == 0
    before = (tmp_path / "npm.json").read_bytes()

    assert _run(tmp_path) == 0
    assert (tmp_path / "npm.json").read_bytes() == before


def test_new_ranking_replaces_the_file(upstream: respx.MockRouter, tmp_path: Path) -> None:
    assert _run(tmp_path, limit=2) == 0
    assert _run(tmp_path, limit=3) == 0

    assert load_popular_packages(tmp_path / "pypi.json").packages == (
        "boto3",
        "requests",
        "django-rest-framework",
    )


def test_invalid_existing_file_is_replaced(upstream: respx.MockRouter, tmp_path: Path) -> None:
    (tmp_path / "pypi.json").write_text("not json", encoding="utf-8")

    assert _run(tmp_path, "--ecosystem", "pypi") == 0
    assert load_popular_packages(tmp_path / "pypi.json").packages[0] == "boto3"


def test_one_ecosystem_only_calls_its_source(upstream: respx.MockRouter, tmp_path: Path) -> None:
    assert _run(tmp_path, "--ecosystem", "pypi") == 0

    assert (tmp_path / "pypi.json").exists()
    assert not (tmp_path / "npm.json").exists()
    assert not upstream["packument"].called


def test_tampered_tarball_fails_and_writes_nothing(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    tampered = _tarball({NPM_LIST_MEMBER: _js_list(["evil", "worse", "worst"])})
    upstream["tarball"].mock(return_value=httpx.Response(200, content=tampered))

    assert _run(tmp_path) == 1
    assert list(tmp_path.iterdir()) == []


def test_tarball_outside_the_registry_is_never_downloaded(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    elsewhere = "https://mirror.example.test/npm-high-impact-9.9.9.tgz"
    route = upstream.get(elsewhere).mock(return_value=httpx.Response(200, content=NPM_TARBALL))
    upstream["packument"].mock(
        return_value=httpx.Response(200, json=_packument(NPM_TARBALL, url=elsewhere))
    )

    assert _run(tmp_path, "--ecosystem", "npm") == 1
    assert not route.called


def test_missing_latest_release_fails(upstream: respx.MockRouter, tmp_path: Path) -> None:
    upstream["packument"].mock(
        return_value=httpx.Response(200, json=_packument(NPM_TARBALL, latest=False))
    )

    assert _run(tmp_path, "--ecosystem", "npm") == 1


def test_source_with_too_few_names_fails_and_writes_nothing(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    assert _run(tmp_path, limit=10) == 1
    assert list(tmp_path.iterdir()) == []


def test_http_error_fails(upstream: respx.MockRouter, tmp_path: Path) -> None:
    upstream["pypi"].mock(return_value=httpx.Response(503))

    assert _run(tmp_path) == 1


def test_parse_js_string_array_reads_both_quote_styles() -> None:
    source = "export const other = ['x']\nexport const topDownload = ['a', \"@b/c\",\n]\n"

    assert parse_js_string_array(source, "topDownload") == ["a", "@b/c"]


@pytest.mark.parametrize(
    "source",
    [
        "export const somethingElse = ['a']",
        "export const topDownload = ['a', b, 'c']",
        "export const topDownload = ['a', // comment\n 'b']",
    ],
)
def test_parse_js_string_array_rejects_unexpected_content(source: str) -> None:
    with pytest.raises(FetchError):
        parse_js_string_array(source, "topDownload")


def test_read_tar_member_rejects_missing_irregular_and_corrupt_archives() -> None:
    with pytest.raises(FetchError, match="missing"):
        read_tar_member(_tarball({"package/other.js": b""}), NPM_LIST_MEMBER)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        directory = tarfile.TarInfo(NPM_LIST_MEMBER)
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
    with pytest.raises(FetchError, match="regular file"):
        read_tar_member(buffer.getvalue(), NPM_LIST_MEMBER)

    with pytest.raises(FetchError, match="cannot be read"):
        read_tar_member(b"not a tarball", NPM_LIST_MEMBER)


def test_verify_integrity_accepts_any_matching_sha512_entry() -> None:
    data = b"payload"

    verify_integrity(data, f"sha1-abc {_integrity(b'other')} {_integrity(data)}")
    with pytest.raises(FetchError, match="no sha512"):
        verify_integrity(data, "sha1-abc")
    with pytest.raises(FetchError, match="does not match"):
        verify_integrity(data, _integrity(b"other"))


def test_parse_naive_utc_keeps_an_explicit_offset() -> None:
    parsed = parse_naive_utc("2026-10-01T12:00:00+02:00")

    assert parsed == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def test_limit_must_be_positive() -> None:
    assert parse_args([]).limit == fetch_popular.DEFAULT_LIMIT
    assert parse_args(["--limit", "1"]).limit == 1
    with pytest.raises(SystemExit):
        parse_args(["--limit", "0"])


# --- Cuarentena de entrantes ---


def _pypi_ranking(*names: str) -> httpx.Response:
    """Volcado de PyPI con ``names`` del más descargado al menos."""
    rows = [{"project": name, "download_count": 1000 - i} for i, name in enumerate(names)]
    return httpx.Response(200, json={"last_update": "2026-11-01 00:00:00", "rows": rows})


def _seed_pypi(upstream: respx.MockRouter, output_dir: Path) -> None:
    """Deja en ``output_dir`` una lista anterior con ``python-dateutil``."""
    upstream["pypi"].mock(return_value=_pypi_ranking("python-dateutil", "requests", "boto3"))
    assert _run(output_dir, "--ecosystem", "pypi") == 0


def test_lookalike_newcomer_stays_out_and_is_reported(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    _seed_pypi(upstream, tmp_path)
    upstream["pypi"].mock(
        return_value=_pypi_ranking(
            "python-dateutil", "requests", "python3-dateutil", "boto3", "numpy", "attrs"
        )
    )
    report = tmp_path / "out" / "report.md"

    assert _run(tmp_path, "--ecosystem", "pypi", "--report", str(report), limit=4) == 0

    packages = load_popular_packages(tmp_path / "pypi.json").packages
    assert packages == ("python-dateutil", "requests", "boto3", "numpy")
    text = report.read_text(encoding="utf-8")
    assert "Entran 1, salen 0, en cuarentena 1." in text
    assert "| `python3-dateutil` | 3 | `python-dateutil` | 1 |" in text
    assert "Nombres que entran (1)" in text
    assert "Cómo aprobar un nombre en cuarentena" in text
    assert "\r\n" not in report.read_bytes().decode("utf-8")


def test_allowlisted_newcomer_enters_the_list(upstream: respx.MockRouter, tmp_path: Path) -> None:
    _seed_pypi(upstream, tmp_path)
    upstream["pypi"].mock(
        return_value=_pypi_ranking("python-dateutil", "requests", "python3-dateutil", "boto3")
    )
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(
        "version: 1\npypi:\n  - name: python3-dateutil\n    reason: prueba\n", encoding="utf-8"
    )
    report = tmp_path / "report.md"

    code = _run(
        tmp_path,
        "--ecosystem",
        "pypi",
        "--allowlist",
        str(allowlist),
        "--report",
        str(report),
        limit=4,
    )

    assert code == 0
    packages = load_popular_packages(tmp_path / "pypi.json").packages
    assert packages == ("python-dateutil", "requests", "python3-dateutil", "boto3")
    text = report.read_text(encoding="utf-8")
    assert "en cuarentena 0." in text
    assert "Cómo aprobar" not in text


def test_quarantine_that_empties_the_ranking_fails_and_keeps_the_list(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    _seed_pypi(upstream, tmp_path)
    before = (tmp_path / "pypi.json").read_bytes()
    upstream["pypi"].mock(
        return_value=_pypi_ranking("python-dateutil", "requests", "python3-dateutil", "boto3")
    )

    assert _run(tmp_path, "--ecosystem", "pypi", limit=4) == 1
    assert (tmp_path / "pypi.json").read_bytes() == before


def test_first_generation_reports_no_review(upstream: respx.MockRouter, tmp_path: Path) -> None:
    report = tmp_path / "report.md"

    assert _run(tmp_path, "--report", str(report)) == 0

    text = report.read_text(encoding="utf-8")
    assert "### npm" in text
    assert "### pypi" in text
    assert text.count("Primera generación") == 2
    assert "Cómo aprobar" not in text


def test_list_of_another_ecosystem_is_not_used_as_previous(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    assert _run(tmp_path, "--ecosystem", "npm") == 0
    (tmp_path / "pypi.json").write_bytes((tmp_path / "npm.json").read_bytes())

    assert _run(tmp_path, "--ecosystem", "pypi") == 0
    assert load_popular_packages(tmp_path / "pypi.json").packages[0] == "boto3"


def test_missing_allowlist_fails_and_writes_nothing(
    upstream: respx.MockRouter, tmp_path: Path
) -> None:
    output_dir = tmp_path / "lists"

    assert _run(output_dir, "--allowlist", str(tmp_path / "missing.yaml")) == 1
    assert not output_dir.exists()
    assert not upstream["pypi"].called


def test_long_reports_are_truncated(
    upstream: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_popular, "REPORT_MAX_HELD_ROWS", 1)
    monkeypatch.setattr(fetch_popular, "REPORT_MAX_ENTERED_NAMES", 1)
    _seed_pypi(upstream, tmp_path)
    upstream["pypi"].mock(
        return_value=_pypi_ranking(
            "python-dateutil", "python3-dateutil", "pythondateutil", "numpy", "attrs", "boto3"
        )
    )
    report = tmp_path / "report.md"

    assert _run(tmp_path, "--ecosystem", "pypi", "--report", str(report), limit=4) == 0

    text = report.read_text(encoding="utf-8")
    assert "en cuarentena 2." in text
    assert "`python3-dateutil`" in text
    assert "`pythondateutil`" not in text
    assert "… y 1 más." in text
    assert "`numpy` … y 1 más." in text
