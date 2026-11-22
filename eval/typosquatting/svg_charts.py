"""Gráficas de línea en SVG para los resultados de la medición, sin dependencias externas.

Las curvas son escalonadas: una métrica medida con el umbral ``t`` vale lo mismo
hasta el siguiente umbral medido, porque los puntajes del detector forman mesetas.

Los colores van como atributos (tema claro), así el SVG se ve igual en un
convertidor sin CSS; el bloque ``<style>`` los cambia al tema oscuro cuando el
visor lo pide. Son los tres primeros colores categóricos de la paleta de
referencia, validados para daltonismo en ambos temas. El texto nunca usa el color de
una serie: la identidad va en la muestra de la leyenda. La leyenda trae además el
valor de cada serie en la línea de referencia (el umbral vigente), que también se
marca con un punto sobre cada curva.
"""

import html
import math
from collections.abc import Callable, Sequence
from typing import NamedTuple

WIDTH = 720
HEIGHT = 420
_LEFT = 64
_RIGHT = 32
_TOP = 104
_BOTTOM = 60
_PLOT_WIDTH = WIDTH - _LEFT - _RIGHT
_PLOT_HEIGHT = HEIGHT - _TOP - _BOTTOM
_LEGEND_Y = 76
_HIT_RADIUS = 9
_MAX_TICKS = 6
_NICE_STEPS = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.25)
_ONE_DECIMAL_BELOW = 0.1
_CHAR_WIDTH = 7.0  # Ancho medio de un carácter de 12 px, para separar la leyenda.


def escape(text: str) -> str:
    """Escapa ``&``, ``<`` y ``>`` para escribir ``text`` dentro del SVG.

    Es lo mismo que ``xml.sax.saxutils.escape``. No se importa ``xml.sax`` porque
    bandit (B406) lo marca: ese módulo también trae parsers, aunque aquí solo se
    escribe XML y nunca se lee.
    """
    return html.escape(text, quote=False)


SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")
_DARK_SERIES_COLORS = ("#3987e5", "#d95926", "#199e70")

_STYLE = """
@media (prefers-color-scheme: dark) {
  .surface { fill: #1a1a19; }
  .ink { fill: #f0efec; }
  .ink-2 { fill: #c3c2b7; }
  .muted { fill: #898781; }
  .grid { stroke: #2c2c2a; }
  .axis { stroke: #383835; }
  .marker { stroke: #898781; }
  .ring { stroke: #1a1a19; }
%s}
"""

Point = tuple[float, float | None]
Formatter = Callable[[float], str]


class Series(NamedTuple):
    """Una serie: su nombre en la leyenda y sus puntos ``(x, y)``; ``y=None`` corta la línea."""

    label: str
    points: Sequence[Point]


class Marker(NamedTuple):
    """Línea vertical de referencia, por ejemplo el umbral de bloqueo vigente."""

    x: float
    label: str


class Chart(NamedTuple):
    """Todo lo que describe una gráfica de líneas."""

    title: str
    subtitle: str
    description: str
    series: Sequence[Series]
    x_range: tuple[float, float]
    x_ticks: Sequence[float]
    x_label: str
    x_format: Formatter
    y_max: float
    y_format: Formatter
    marker: Marker | None = None


def decimal(value: float, digits: int = 2) -> str:
    """Número con coma decimal, como se escribe en los documentos del equipo."""
    return f"{value:.{digits}f}".replace(".", ",")


def percent(value: float) -> str:
    """Porcentaje con una cifra decimal bajo 10 % y entero desde ahí (``1,5 %``, ``31 %``)."""
    digits = 1 if abs(value) < _ONE_DECIMAL_BELOW else 0
    text = decimal(value * 100, digits).removesuffix(",0")
    return f"{text} %"


def nice_axis_max(value: float) -> tuple[float, float]:
    """Tope del eje y y su paso: el múltiplo de un paso redondo que cubre ``value``."""
    for step in _NICE_STEPS:
        # El épsilon evita que 0,2 / 0,05 = 4,0000001 sume un paso de más.
        ticks = math.ceil(max(value, step) / step - 1e-9)
        if ticks <= _MAX_TICKS - 1:
            return round(step * ticks, 6), step
    return 1.0, 0.2


def value_at(series: Series, x: float) -> float | None:
    """Valor de la curva escalonada en ``x``: el del último punto medido hasta ``x``."""
    measured = [(px, py) for px, py in sorted(series.points) if px <= x]
    return measured[-1][1] if measured else None


def render(chart: Chart) -> str:
    """Devuelve el documento SVG de ``chart``."""
    if len(chart.series) > len(SERIES_COLORS):
        msg = f"at most {len(SERIES_COLORS)} series per chart"
        raise ValueError(msg)
    x_min, x_max = chart.x_range

    def sx(x: float) -> float:
        return _LEFT + (x - x_min) / (x_max - x_min) * _PLOT_WIDTH

    def sy(y: float) -> float:
        return _TOP + _PLOT_HEIGHT - y / chart.y_max * _PLOT_HEIGHT

    _, y_step = nice_axis_max(chart.y_max)
    parts = [_header(chart)]
    parts += _grid(chart, sx, sy, y_step)
    if chart.marker is not None:
        parts += _marker(chart.marker, sx)
    for index, series in enumerate(chart.series):
        parts += _series(index, series, chart, sx, sy)
    parts += _legend(chart)
    parts.append("</svg>\n")
    return "\n".join(parts)


def _header(chart: Chart) -> str:
    dark = "".join(
        f"  .s{index} {{ stroke: {color}; }}\n  .d{index} {{ fill: {color}; }}\n"
        for index, color in enumerate(_DARK_SERIES_COLORS)
    )
    font = 'font-family="system-ui, -apple-system, &quot;Segoe UI&quot;, sans-serif"'
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
            f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="t d" {font}>',
            f'<title id="t">{escape(chart.title)}</title>',
            f'<desc id="d">{escape(chart.description)}</desc>',
            f"<style>{_STYLE % dark}</style>",
            f'<rect class="surface" width="{WIDTH}" height="{HEIGHT}" fill="#fcfcfb"/>',
            f'<text class="ink" x="{_LEFT}" y="28" font-size="16" font-weight="600" '
            f'fill="#0b0b0b">{escape(chart.title)}</text>',
            f'<text class="ink-2" x="{_LEFT}" y="48" font-size="13" '
            f'fill="#52514e">{escape(chart.subtitle)}</text>',
        ]
    )


def _grid(
    chart: Chart, sx: Callable[[float], float], sy: Callable[[float], float], y_step: float
) -> list[str]:
    parts: list[str] = []
    count = round(chart.y_max / y_step)
    for i in range(count + 1):
        value = i * y_step
        y = sy(value)
        css = "axis" if i == 0 else "grid"
        color = "#c3c2b7" if i == 0 else "#e1e0d9"
        parts.append(
            f'<line class="{css}" x1="{_LEFT}" x2="{_LEFT + _PLOT_WIDTH}" y1="{y:.1f}" '
            f'y2="{y:.1f}" stroke="{color}" stroke-width="1"/>'
        )
        parts.append(
            f'<text class="muted" x="{_LEFT - 8}" y="{y + 4:.1f}" font-size="12" '
            f'text-anchor="end" fill="#898781" style="font-variant-numeric: tabular-nums">'
            f"{escape(chart.y_format(value))}</text>"
        )
    baseline = _TOP + _PLOT_HEIGHT
    for tick in chart.x_ticks:
        x = sx(tick)
        parts.append(
            f'<text class="muted" x="{x:.1f}" y="{baseline + 20}" font-size="12" '
            f'text-anchor="middle" fill="#898781" style="font-variant-numeric: tabular-nums">'
            f"{escape(chart.x_format(tick))}</text>"
        )
    parts.append(
        f'<text class="ink-2" x="{_LEFT + _PLOT_WIDTH / 2:.1f}" y="{HEIGHT - 14}" '
        f'font-size="12" text-anchor="middle" fill="#52514e">{escape(chart.x_label)}</text>'
    )
    return parts


def _marker(marker: Marker, sx: Callable[[float], float]) -> list[str]:
    x = sx(marker.x)
    return [
        f'<line class="marker" x1="{x:.1f}" x2="{x:.1f}" y1="{_TOP - 6}" '
        f'y2="{_TOP + _PLOT_HEIGHT}" stroke="#898781" stroke-width="1"/>',
        f'<text class="muted" x="{x + 6:.1f}" y="{_TOP - 8}" font-size="12" '
        f'fill="#898781">{escape(marker.label)}</text>',
    ]


def _segments(points: Sequence[Point]) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = [[]]
    for x, y in sorted(points):
        if y is None:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append((x, y))
    return [segment for segment in segments if segment]


def _series(
    index: int,
    series: Series,
    chart: Chart,
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> list[str]:
    color = SERIES_COLORS[index]
    parts: list[str] = []
    for segment in _segments(series.points):
        (x0, y0), *rest = segment
        path = [f"M{sx(x0):.1f},{sy(y0):.1f}"]
        for x, y in rest:
            path.append(f"H{sx(x):.1f}V{sy(y):.1f}")
        parts.append(
            f'<path class="s{index}" d="{"".join(path)}" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    if chart.marker is not None:
        current = value_at(series, chart.marker.x)
        if current is not None:
            parts.append(
                f'<circle class="d{index} ring" cx="{sx(chart.marker.x):.1f}" '
                f'cy="{sy(current):.1f}" r="4" fill="{color}" stroke="#fcfcfb" stroke-width="2"/>'
            )
    # Zonas invisibles para leer cada valor al pasar el puntero (``<title>`` nativo).
    for point_x, point_y in series.points:
        if point_y is None:
            continue
        value = f"{chart.x_format(point_x)}: {chart.y_format(point_y)}"
        text = f"{series.label} · {chart.x_label} {value}"
        parts.append(
            f'<circle cx="{sx(point_x):.1f}" cy="{sy(point_y):.1f}" r="{_HIT_RADIUS}" '
            f'fill="transparent"><title>{escape(text)}</title></circle>'
        )
    return parts


def _legend(chart: Chart) -> list[str]:
    parts: list[str] = []
    x = float(_LEFT)
    if chart.marker is not None:
        intro = f"Con {chart.x_format(chart.marker.x)}:"
        parts.append(
            f'<text class="ink-2" x="{x:.1f}" y="{_LEGEND_Y}" font-size="12" '
            f'fill="#52514e">{escape(intro)}</text>'
        )
        x += _CHAR_WIDTH * len(intro) + 12
    for index, series in enumerate(chart.series):
        label = series.label
        if chart.marker is not None:
            current = value_at(series, chart.marker.x)
            label += " —" if current is None else f" {chart.y_format(current)}"
        parts.append(
            f'<line class="s{index}" x1="{x:.1f}" x2="{x + 16:.1f}" y1="{_LEGEND_Y - 4}" '
            f'y2="{_LEGEND_Y - 4}" stroke="{SERIES_COLORS[index]}" stroke-width="2" '
            'stroke-linecap="round"/>'
        )
        parts.append(
            f'<text class="ink-2" x="{x + 22:.1f}" y="{_LEGEND_Y}" font-size="12" '
            f'fill="#52514e">{escape(label)}</text>'
        )
        x += 22 + _CHAR_WIDTH * len(label) + 20
    return parts
