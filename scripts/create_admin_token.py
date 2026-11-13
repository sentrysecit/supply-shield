"""Genera un token de administrador y el hash que se configura en el servicio.

Uso::

    uv run python scripts/create_admin_token.py

Imprime el token (guárdalo en un gestor de secretos: no se vuelve a mostrar) y la
línea ``SUPPLY_SHIELD_ADMIN_TOKEN_HASH=...`` para el ``.env`` o el gestor de
secretos del despliegue. El servicio solo guarda el hash, nunca el token.
"""

import secrets

from supply_shield.api.auth import hash_token

TOKEN_PREFIX = "ssa_"  # nosec B105  # noqa: S105
TOKEN_BYTES = 32


def generate_admin_token() -> tuple[str, str]:
    """Devuelve un token nuevo de 256 bits y su hash pbkdf2."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def main() -> None:
    """Imprime el token y la variable de entorno con su hash."""
    token, token_hash = generate_admin_token()
    print("Token de administrador (guárdalo ahora; no se vuelve a mostrar):")
    print(f"  {token}")
    print()
    print("Agrega esta línea al .env (con comillas simples: el hash lleva '$'):")
    print(f"  SUPPLY_SHIELD_ADMIN_TOKEN_HASH='{token_hash}'")


if __name__ == "__main__":
    main()
