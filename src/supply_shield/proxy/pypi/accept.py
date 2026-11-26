"""Negociación del formato del índice simple a partir del header ``Accept``.

PEP 691 define el tipo ``application/vnd.pypi.simple.v1+json`` para el índice en
JSON; PEP 503 usa ``text/html``. Un cliente pide el formato con ``Accept`` y sus
``q`` values. Se elige JSON solo si el cliente lo acepta al menos tanto como el
HTML; en cualquier otro caso se sirve HTML, que es el formato que entienden
todos los clientes de pip y uv.
"""

from supply_shield.proxy.pypi.models import IndexFormat

JSON_MEDIA_TYPE = "application/vnd.pypi.simple.v1+json"
HTML_MEDIA_TYPE = "text/html"

_JSON_TYPES = frozenset({JSON_MEDIA_TYPE, "application/json"})
_HTML_TYPES = frozenset({HTML_MEDIA_TYPE, "application/vnd.pypi.simple.v1+html", "text/*"})
_WILDCARD = "*/*"


def _quality(entry: str) -> float:
    """Devuelve el ``q`` de una entrada de ``Accept`` (1.0 si no lo declara)."""
    _media, *params = entry.split(";")
    for param in params:
        key, _, value = param.partition("=")
        if key.strip().lower() != "q":
            continue
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 1.0


def _best_quality(accept: str, accepted: frozenset[str], *, allow_wildcard: bool) -> float:
    """Mayor ``q`` con el que ``accept`` acepta alguno de los tipos de un grupo.

    El comodín ``*/*`` cuenta solo donde ``allow_wildcard`` lo permite: para elegir
    JSON hace falta que el cliente lo pida explícitamente, de modo que un cliente
    que solo envía ``*/*`` recibe HTML (el formato de PEP 503).
    """
    best = 0.0
    for entry in accept.split(","):
        media = entry.split(";", 1)[0].strip().lower()
        if media in accepted or (allow_wildcard and media == _WILDCARD):
            best = max(best, _quality(entry))
    return best


def choose_index_format(accept: str | None) -> IndexFormat:
    """Elige el formato del índice simple que pide el header ``Accept``.

    Sin header, o con solo comodines, se sirve HTML (PEP 503). JSON gana solo si el
    cliente lo pide explícitamente con un ``q`` mayor o igual al del HTML y distinto
    de cero.
    """
    if not accept:
        return IndexFormat.HTML
    json_quality = _best_quality(accept, _JSON_TYPES, allow_wildcard=False)
    html_quality = _best_quality(accept, _HTML_TYPES, allow_wildcard=True)
    if json_quality <= 0 or json_quality < html_quality:
        return IndexFormat.HTML
    return IndexFormat.JSON
