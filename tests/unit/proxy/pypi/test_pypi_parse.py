"""Unit tests for parsing the upstream Simple Repository API (PEP 503/691)."""

from supply_shield.proxy.pypi.parse import parse_simple_html, parse_simple_json

HTML = """
<!DOCTYPE html>
<html>
  <body>
    <h1>Links for django</h1>
    <a href="https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl#sha256=aaa1">Django-5.0-py3-none-any.whl</a>
    <a href="https://files.pythonhosted.org/packages/ef/01/Django-5.0.tar.gz#sha256=bbb2"
       data-requires-python="&gt;=3.10"
       data-core-metadata="sha256=ccc3">Django-5.0.tar.gz</a>
    <a href="https://files.pythonhosted.org/packages/99/99/Django-4.2.tar.gz#sha256=ddd4"
       data-yanked="broken release">Django-4.2.tar.gz</a>
  </body>
</html>
"""

JSON = {
    "meta": {"api-version": "1.1"},
    "name": "django",
    "files": [
        {
            "filename": "Django-5.0-py3-none-any.whl",
            "url": "https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl",
            "hashes": {"sha256": "aaa1"},
            "requires-python": ">=3.10",
            "core-metadata": {"sha256": "ccc3"},
            "yanked": False,
        },
        {
            "filename": "Django-4.2.tar.gz",
            "url": "https://files.pythonhosted.org/packages/99/99/Django-4.2.tar.gz",
            "hashes": {"sha256": "ddd4"},
            "yanked": "broken release",
        },
    ],
}


def test_parse_html_extracts_files() -> None:
    files = parse_simple_html(HTML)
    assert [file.filename for file in files] == [
        "Django-5.0-py3-none-any.whl",
        "Django-5.0.tar.gz",
        "Django-4.2.tar.gz",
    ]


def test_parse_html_reads_sha256_fragment_and_pep658() -> None:
    files = parse_simple_html(HTML)
    wheel, sdist, yanked = files
    assert wheel.sha256 == "aaa1"
    assert wheel.requires_python is None
    assert wheel.core_metadata is False
    assert sdist.requires_python == ">=3.10"
    assert sdist.core_metadata is True
    assert sdist.metadata_sha256 == "ccc3"
    assert yanked.yanked is True
    assert yanked.yanked_reason == "broken release"


def test_parse_json_extracts_files() -> None:
    files = parse_simple_json(JSON)
    wheel, yanked = files
    assert wheel.filename == "Django-5.0-py3-none-any.whl"
    assert wheel.sha256 == "aaa1"
    assert wheel.requires_python == ">=3.10"
    assert wheel.core_metadata is True
    assert wheel.metadata_sha256 == "ccc3"
    assert wheel.yanked is False
    assert yanked.yanked_reason == "broken release"


def test_parse_json_ignores_unusable_entries() -> None:
    files = parse_simple_json({"files": [{"filename": "x"}, "not-a-dict"]})
    assert files == ()


def test_parse_json_tolerates_missing_files() -> None:
    assert parse_simple_json({"name": "django"}) == ()
    assert parse_simple_json("not a mapping") == ()
