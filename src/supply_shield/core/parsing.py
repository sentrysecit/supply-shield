"""Utilidades para convertir la entrada cruda de un validador en campos tipados."""


def string_key_dict(data: object) -> dict[str, object] | None:
    """Copia un mapeo cuando todas las claves son cadenas.

    Devuelve ``None`` si ``data`` no es un dict o alguna clave no es una cadena,
    para que quien llama deje esa entrada y Pydantic la rechace.
    """
    if not isinstance(data, dict):
        return None
    copied: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            return None
        copied[key] = value
    return copied


def required_text(label: str, value: str) -> str:
    """Quita el espacio de ``value`` y rechaza una cadena en blanco."""
    stripped = value.strip()
    if not stripped:
        msg = f"{label} must not be blank"
        raise ValueError(msg)
    return stripped
