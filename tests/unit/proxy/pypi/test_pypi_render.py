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
    assert "http://proxy.test/packages/ab/cd/Django-5.0-py3-none-any.whl" in document
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
    assert payload["meta"] == {"api-version": "1.1"}
    assert payload["name"] == "django"
    files = payload["files"]
    assert isinstance(files, list)
    wheel = files[0]
    assert wheel["url"].startswith("http://proxy.test/packages/")
    assert wheel["url"].endswith("#sha256=aaa1")
    assert wheel["hashes"] == {"sha256": "aaa1"}
    assert wheel["requires-python"] == ">=3.10"
    assert wheel["core-metadata"] == {"sha256": "ccc3"}
    assert wheel["yanked"] is False
    assert files[1]["yanked"] == "broken release"
