"""Script que genera un token de proyecto y su entrada de ``config/projects.yaml``."""

import re

import pytest
import yaml

from create_project_token import main, projects_entry
from supply_shield.api.auth import ProjectsFile, issue_project_token, verify_token

TOKEN = re.compile(r"ssp_[0-9a-f]{16}_[A-Za-z0-9_-]{43}")


def test_main_prints_the_token_and_a_valid_projects_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["frontend"])

    out = capsys.readouterr().out
    match = TOKEN.search(out)
    assert match is not None
    entry = out[out.index("  - name:") :].split("\n\n")[0]
    projects = ProjectsFile.model_validate({"projects": yaml.safe_load(entry)})
    assert projects.projects[0].name == "frontend"
    assert verify_token(match.group(), projects.projects[0].token_hash)


@pytest.mark.parametrize("name", ['mi "proyecto": x', "ñandú", "a\\b"])
def test_the_entry_survives_names_with_yaml_syntax(name: str) -> None:
    issued = issue_project_token()

    parsed = yaml.safe_load(projects_entry(name, issued))

    assert parsed == [{"name": name, "token_hash": issued.token_hash}]


def test_a_blank_project_name_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["   "])

    assert "vacío" in capsys.readouterr().err


def test_the_project_name_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])

    assert "project" in capsys.readouterr().err
