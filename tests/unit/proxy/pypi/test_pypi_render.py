"""Unit tests for rendering the Simple Repository API response (PEP 503/691)."""

from supply_shield.proxy.pypi.models import DistributionFile, SimpleProject
from supply_shield.proxy.pypi.render import render_html, render_json

BASE = "http://proxy.test/"


def _project() -> SimpleProject:
    return SimpleProject(
        name="django",
        files=(
            DistributionFile(
                filename="Django-5.0-py3-none-any.whl",
                url="https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl",
                sha256="aaa1",
                requires_python=">=3.10",
                core_metadata=True,
                metadata_sha256="ccc3",
            ),
            DistributionFile(
                filename="Django-4.2.tar.gz",
                url="https://files.pythonhosted.org/packages/99/99/Django-4.2.tar.gz",
                sha256="ddd4",
                yanked=True,
                yanked_reason="broken release",
            ),
        ),
    )


def test_render_html_rewrites_links_and_keeps_attributes() -> None:
    document = render_html(_project(), BASE)
    assert "Links for django" in document
    assert (
        "http://proxy.test/pypi/packages/sha256/aaa1/ab/cd/Django-5.0-py3-none-any.whl" in document
    )
    assert "#sha256=aaa1" in document
    assert 'data-requires-python="&gt;=3.10"' in document
    assert 'data-core-metadata="sha256=ccc3"' in document
    assert 'data-yanked="broken release"' in document


def test_render_html_escapes_names() -> None:
    project = SimpleProject(name="django", files=())
    document = render_html(project, BASE)
    assert "<h1>Links for django</h1>" in document


def test_render_json_uses_proxy_urls_and_pep691_shape() -> None:
    payload = render_json(_project(), BASE)
    assert payload["meta"] == {"api-version": "1.0"}
    assert payload["name"] == "django"
    files = payload["files"]
    assert isinstance(files, list)
    wheel = files[0]
    assert wheel["url"].startswith("http://proxy.test/pypi/packages/")
    assert wheel["url"].endswith("#sha256=aaa1")
    assert wheel["hashes"] == {"sha256": "aaa1"}
    assert wheel["requires-python"] == ">=3.10"
    assert wheel["core-metadata"] == {"sha256": "ccc3"}
    assert wheel["yanked"] is False
    assert files[1]["yanked"] == "broken release"


def _file(**overrides: object) -> DistributionFile:
    base: dict[str, object] = {
        "filename": "x-1.0-py3-none-any.whl",
        "url": "https://files.pythonhosted.org/packages/ab/cd/x-1.0-py3-none-any.whl",
    }
    base.update(overrides)
    return DistributionFile.model_validate(base)


def test_files_the_proxy_cannot_serve_are_dropped_from_both_formats() -> None:
    """Fail-closed: un enlace a otro host no se entrega, porque saltaría el firewall."""
    project = SimpleProject(
        name="x",
        files=(
            _file(sha256="aaa1"),
            _file(filename="evil-1.0.whl", url="https://cdn.example/evil-1.0.whl#sha256=bbb2"),
        ),
    )
    document = render_html(project, BASE)
    assert "cdn.example" not in document
    assert "evil-1.0.whl" not in document
    assert "x-1.0-py3-none-any.whl" in document
    payload = render_json(project, BASE)
    files = payload["files"]
    assert isinstance(files, list)
    assert [file["filename"] for file in files] == ["x-1.0-py3-none-any.whl"]


def test_render_json_declares_1_1_with_pep700_fields() -> None:
    project = SimpleProject(
        name="x",
        versions=("0.9", "1.0"),
        files=(_file(sha256="aaa1", size=1234, upload_time="2023-12-04T09:00:00.000000Z"),),
    )
    payload = render_json(project, BASE)
    assert payload["meta"] == {"api-version": "1.1"}
    assert payload["versions"] == ["0.9", "1.0"]
    files = payload["files"]
    assert isinstance(files, list)
    assert files[0]["size"] == 1234
    assert files[0]["upload-time"] == "2023-12-04T09:00:00.000000Z"


def test_render_json_falls_back_to_1_0_without_versions_or_sizes() -> None:
    """Sin ``versions`` o sin ``size`` no se cumple la PEP 700, así que se declara 1.0."""
    without_versions = SimpleProject(name="x", files=(_file(size=10),))
    without_sizes = SimpleProject(name="x", versions=("1.0",), files=(_file(),))
    assert render_json(without_versions, BASE)["meta"] == {"api-version": "1.0"}
    assert "versions" not in render_json(without_versions, BASE)
    assert render_json(without_sizes, BASE)["meta"] == {"api-version": "1.0"}


def test_render_json_always_includes_hashes() -> None:
    """PEP 691: la clave ``hashes`` va siempre, aunque el upstream no trajera hash."""
    payload = render_json(SimpleProject(name="x", files=(_file(),)), BASE)
    files = payload["files"]
    assert isinstance(files, list)
    assert files[0]["hashes"] == {}
    assert "size" not in files[0]
    assert "upload-time" not in files[0]
