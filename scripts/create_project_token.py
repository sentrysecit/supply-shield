"""Genera un token de proyecto y la entrada de ``config/projects.yaml`` con su hash.

Uso::

    uv run python scripts/create_project_token.py frontend

Imprime el token (guárdalo como secreto del pipeline o en un gestor de secretos:
no se vuelve a mostrar) y el bloque YAML con su hash. Si el proyecto ya existe en
``config/projects.yaml``, reemplaza solo su ``token_hash`` y reinicia el servicio.

El token tiene el mismo formato que los que crea la API de administración
(``POST /api/v1/admin/projects/{project}/tokens``), que guarda el hash en la base.
"""

import argparse
import json
from collections.abc import Sequence

from supply_shield.api.auth import IssuedProjectToken, issue_project_token


def projects_entry(project: str, issued: IssuedProjectToken) -> str:
    """Bloque YAML para agregar el proyecto a ``config/projects.yaml``.

    Los valores van entre comillas dobles con escapes JSON, que también son YAML
    válido: un nombre con comillas o dos puntos no rompe el archivo.
    """
    name = json.dumps(project, ensure_ascii=False)
    token_hash = json.dumps(issued.token_hash)
    return f"  - name: {name}\n    token_hash: {token_hash}"


def _project_name(value: str) -> str:
    name = value.strip()
    if not name:
        msg = "el nombre del proyecto no puede estar vacío"
        raise argparse.ArgumentTypeError(msg)
    return name


def main(argv: Sequence[str] | None = None) -> None:
    """Imprime un token nuevo para el proyecto y su entrada de ``projects.yaml``."""
    parser = argparse.ArgumentParser(
        description="Genera un token de proyecto y su hash para config/projects.yaml."
    )
    parser.add_argument("project", type=_project_name, help="nombre del proyecto")
    args = parser.parse_args(argv)
    project: str = args.project

    issued = issue_project_token()
    print(f"Token del proyecto {project!r} (guárdalo ahora; no se vuelve a mostrar):")
    print(f"  {issued.token.get_secret_value()}")
    print()
    print("Agrega esto bajo 'projects:' en config/projects.yaml")
    print("(si el proyecto ya existe, cambia solo su token_hash):")
    print()
    print(projects_entry(project, issued))
    print()
    print("Reinicia el servicio para que tome el cambio.")


if __name__ == "__main__":
    main()
