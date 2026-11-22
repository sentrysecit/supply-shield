"""Mide el detector de typosquatting con el dataset etiquetado de D4-6, sin red.

Puntúa cada nombre del dataset con el detector tal como corre en el servicio: las
listas de ``data/popular/``, la lista de permitidos y los pesos actuales. Luego
calcula, para cada umbral de bloqueo entre 0,60 y 1,00, precisión, recall y F1, y la
tasa de falsos positivos de cada grupo de legítimos. Escribe en ``--output-dir``:

- ``metricas.csv``: una fila por ecosistema (y ambos juntos) y umbral;
- ``detalle.csv``: cada typosquat y cada legítimo que el detector marca;
- ``resumen.md``: las tablas que usa ``docs/results/typosquatting.md``;
- ``umbral-<eco>.svg`` y ``falsos-positivos-<eco>.svg``: las gráficas.

Uso:
    uv run python eval/typosquatting/evaluate.py [--dataset-dir DIR] [--output-dir DIR]
"""

import argparse
import csv
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import structlog
from rapidfuzz.distance import DamerauLevenshtein

import svg_charts
from supply_shield.analyzers.typosquatting import (
    SeverityThresholds,
    TyposquatMatch,
    TyposquattingAnalyzer,
)
from supply_shield.analyzers.typosquatting.keys import split_scope
from supply_shield.config import get_settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.package_ref import PackageRef
from supply_shield.logging import configure_logging

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_DIR = Path(__file__).resolve().parent / "dataset"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "results" / "typosquatting"

# Umbrales medidos: de 0,60 (el mínimo que reporta el detector) a 1,00, cada 0,01.
THRESHOLDS: tuple[float, ...] = tuple(step / 100 for step in range(60, 101))
CHART_RANGE = (0.70, 1.00)  # Ningún puntaje queda por debajo de 0,70.
CHART_TICKS = tuple(step / 100 for step in range(70, 101, 5))
EXAMPLES = 12
MISS_EXAMPLES = 6
ALL = "all"
# Para clasificar los typosquats que se escapan: palabras del nombre y la distancia
# máxima a la que una palabra se considera el nombre imitado mal escrito.
_WORD_SEPARATORS = re.compile(r"[-_./@]+")
_MIN_WORD = 4
_MAX_WORD_EDITS = 2

log = structlog.get_logger(__name__)


class Group(StrEnum):
    """Grupo del dataset al que pertenece un nombre."""

    TYPOSQUAT = "typosquat"
    TYPOSQUAT_UNTARGETED = "typosquat_untargeted"
    POPULAR = "popular"
    NEXT = "next"
    RANDOM = "random"


NEGATIVE_GROUPS = (Group.POPULAR, Group.NEXT, Group.RANDOM)


class Miss(StrEnum):
    """Por qué un typosquat no se bloquea, según la forma de su nombre."""

    OUT_OF_LIST = "out_of_list"
    SCOPE = "scope"
    COMBO = "combo"
    COMBO_TYPO = "combo_typo"
    BELOW_THRESHOLD = "below_threshold"
    OTHER = "other"


MISS_LABELS = {
    Miss.OUT_OF_LIST: "El paquete imitado no está en la lista popular",
    Miss.SCOPE: "El nombre, igual o con un error, bajo un `@scope` ajeno (`@x/ws` por `ws`)",
    Miss.COMBO: "Nombre imitado más otras palabras (`chalk-core`)",
    Miss.COMBO_TYPO: "Una palabra del nombre imitado, a veces mal escrita, más otras "
    "(`typescipt-cli`)",
    Miss.BELOW_THRESHOLD: "Se parece, pero el puntaje no llega al umbral",
    Miss.OTHER: "Otra forma",
}

GROUP_LABELS = {
    Group.TYPOSQUAT: "Typosquats con paquete imitado",
    Group.TYPOSQUAT_UNTARGETED: "Typosquats sin paquete imitado identificable",
    Group.POPULAR: "Legítimos: la lista popular",
    Group.NEXT: "Legítimos: siguientes del ranking",
    Group.RANDOM: "Legítimos: muestra aleatoria del registro",
}


class Sample(NamedTuple):
    """Un nombre del dataset con su etiqueta."""

    ecosystem: Ecosystem
    group: Group
    name: str
    target: str | None = None
    rank: int | None = None


class Scored(NamedTuple):
    """Un nombre del dataset y lo que respondió el detector."""

    sample: Sample
    match: TyposquatMatch | None

    @property
    def score(self) -> float:
        """Puntaje del detector; 0 si no se parece a ningún paquete popular."""
        return 0.0 if self.match is None else self.match.score

    def flagged(self, threshold: float) -> bool:
        """Indica si el detector lo bloquearía con ``threshold``."""
        return self.match is not None and self.match.score >= threshold


class Rates(NamedTuple):
    """Resultado de un grupo con un umbral: cuántos marca de cuántos."""

    flagged: int
    total: int

    @property
    def rate(self) -> float | None:
        """Proporción marcada; ``None`` si el grupo está vacío."""
        return self.flagged / self.total if self.total else None


class Metrics(NamedTuple):
    """Métricas de un ecosistema (o de ambos) con un umbral de bloqueo."""

    scope: str
    threshold: float
    tp: int
    fn: int
    fp: int
    tn: int
    in_scope: Rates
    by_group: Mapping[Group, Rates]

    @property
    def precision(self) -> float | None:
        """De lo que se bloquea, cuánto es typosquat."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        """De los typosquats, cuántos se bloquean."""
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        """Media armónica de precisión y recall."""
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_dataset(dataset_dir: Path, ecosystem: Ecosystem) -> list[Sample]:
    """Lee los tres archivos de ``ecosystem`` que escribe ``build_dataset.py``."""
    prefix = dataset_dir / ecosystem.value
    samples = [
        Sample(
            ecosystem,
            Group.TYPOSQUAT if row["target"] else Group.TYPOSQUAT_UNTARGETED,
            row["name"],
            target=row["target"] or None,
        )
        for row in _read_csv(Path(f"{prefix}-typosquats.csv"))
    ]
    samples += [
        Sample(ecosystem, Group.NEXT, row["name"], rank=int(row["rank"]))
        for row in _read_csv(Path(f"{prefix}-next.csv"))
    ]
    samples += [
        Sample(ecosystem, Group.RANDOM, row["name"])
        for row in _read_csv(Path(f"{prefix}-random.csv"))
    ]
    return samples


def popular_samples(analyzer: TyposquattingAnalyzer, ecosystem: Ecosystem) -> list[Sample]:
    """La lista popular como negativos: el criterio "no marca el top N" de D4-4."""
    names = analyzer.indexes[ecosystem].names
    return [
        Sample(ecosystem, Group.POPULAR, name, rank=rank)
        for rank, name in enumerate(names, start=1)
    ]


def score_samples(samples: Iterable[Sample], analyzer: TyposquattingAnalyzer) -> list[Scored]:
    """Puntúa cada nombre con el detector, sin umbrales."""
    return [
        Scored(sample, analyzer.match(PackageRef.of(sample.ecosystem, sample.name)))
        for sample in samples
    ]


def measure(
    scored: Sequence[Scored],
    threshold: float,
    *,
    scope: str,
    popular: Mapping[Ecosystem, frozenset[str]],
) -> Metrics:
    """Matriz de confusión y tasas por grupo de ``scored`` con ``threshold``.

    Los typosquats sin paquete imitado no cuentan: un detector por nombre no tiene
    contra qué compararlos. "En alcance" son los typosquats cuyo paquete imitado está
    en la lista popular, los únicos que el diseño puede detectar.
    """
    flagged: Counter[Group] = Counter()
    totals: Counter[Group] = Counter()
    in_scope = Counter[bool]()
    in_scope_flagged = 0
    for item in scored:
        group = item.sample.group
        totals[group] += 1
        hit = item.flagged(threshold)
        flagged[group] += hit
        if group is Group.TYPOSQUAT:
            reachable = item.sample.target in popular[item.sample.ecosystem]
            in_scope[reachable] += 1
            in_scope_flagged += hit and reachable
    negatives_flagged = sum(flagged[group] for group in NEGATIVE_GROUPS)
    negatives = sum(totals[group] for group in NEGATIVE_GROUPS)
    return Metrics(
        scope=scope,
        threshold=threshold,
        tp=flagged[Group.TYPOSQUAT],
        fn=totals[Group.TYPOSQUAT] - flagged[Group.TYPOSQUAT],
        fp=negatives_flagged,
        tn=negatives - negatives_flagged,
        in_scope=Rates(in_scope_flagged, in_scope[True]),
        by_group={group: Rates(flagged[group], totals[group]) for group in Group},
    )


def sweep(
    scored: Sequence[Scored], popular: Mapping[Ecosystem, frozenset[str]]
) -> dict[str, list[Metrics]]:
    """Métricas de cada ecosistema y de ambos juntos, para cada umbral de ``THRESHOLDS``."""
    scopes: dict[str, list[Scored]] = {ecosystem.value: [] for ecosystem in Ecosystem}
    for item in scored:
        scopes[item.sample.ecosystem.value].append(item)
    scopes[ALL] = list(scored)
    return {
        scope: [measure(items, t, scope=scope, popular=popular) for t in THRESHOLDS]
        for scope, items in scopes.items()
    }


def best_f1(rows: Sequence[Metrics]) -> Metrics:
    """Umbral con mejor F1; ante un empate, el más alto, que bloquea menos legítimos."""
    return max(rows, key=lambda row: (row.f1 or 0.0, row.threshold))


def at(rows: Sequence[Metrics], threshold: float) -> Metrics:
    """Fila de ``rows`` medida con ``threshold``."""
    for row in rows:
        if row.threshold == threshold:
            return row
    msg = f"threshold {threshold} was not measured"
    raise ValueError(msg)


def _words(name: str) -> list[str]:
    return [word for word in _WORD_SEPARATORS.split(name) if word]


def _contains(words: Sequence[str], part: Sequence[str]) -> bool:
    size = len(part)
    return any(list(words[i : i + size]) == list(part) for i in range(len(words) - size + 1))


def miss_reason(name: str, target: str, popular: frozenset[str], *, scored: bool) -> Miss:
    """Clasifica un typosquat que no se bloquea según cómo se forma su nombre.

    Describe el error, no decide nada: sirve para saber qué técnica faltaría para
    detectarlo. ``scored`` indica si el detector lo relacionó con algún popular.
    """
    if target not in popular:
        return Miss.OUT_OF_LIST
    (name_scope, name_bare), (target_scope, target_bare) = split_scope(name), split_scope(target)
    if (
        name_scope is not None
        and name_scope != target_scope
        and DamerauLevenshtein.distance(name_bare, target_bare) <= _MAX_WORD_EDITS
    ):
        return Miss.SCOPE
    name_words, target_words = _words(name_bare), _words(target_bare)
    if _contains(name_words, target_words):
        return Miss.COMBO
    if len(name_words) > 1 and any(
        DamerauLevenshtein.distance(word, other) <= _MAX_WORD_EDITS
        for word in name_words
        for other in target_words
        if min(len(word), len(other)) >= _MIN_WORD
    ):
        return Miss.COMBO_TYPO
    return Miss.BELOW_THRESHOLD if scored else Miss.OTHER


def _ratio(value: float | None, digits: int = 4) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def write_metrics_csv(path: Path, results: Mapping[str, Sequence[Metrics]]) -> None:
    """Una fila por ecosistema y umbral: la tabla de datos detrás de cada gráfica."""
    header = [
        "scope",
        "threshold",
        "tp",
        "fn",
        "fp",
        "tn",
        "precision",
        "recall",
        "f1",
        "recall_in_scope",
        *(f"fpr_{group.value}" for group in NEGATIVE_GROUPS),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for rows in results.values():
            for row in rows:
                writer.writerow(
                    [
                        row.scope,
                        f"{row.threshold:.2f}",
                        row.tp,
                        row.fn,
                        row.fp,
                        row.tn,
                        _ratio(row.precision),
                        _ratio(row.recall),
                        _ratio(row.f1),
                        _ratio(row.in_scope.rate),
                        *(_ratio(row.by_group[group].rate, 5) for group in NEGATIVE_GROUPS),
                    ]
                )


def write_detail_csv(path: Path, scored: Sequence[Scored], report: float) -> None:
    """Cada typosquat y cada legítimo con puntaje reportable, para revisar los errores."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "ecosystem",
                "group",
                "name",
                "expected_target",
                "score",
                "matched_target",
                "matched_rank",
                "techniques",
            ]
        )
        for item in scored:
            positive = item.sample.group in {Group.TYPOSQUAT, Group.TYPOSQUAT_UNTARGETED}
            if not positive and item.score < report:
                continue
            match = item.match
            writer.writerow(
                [
                    item.sample.ecosystem.value,
                    item.sample.group.value,
                    item.sample.name,
                    item.sample.target or "",
                    f"{item.score:.4f}",
                    "" if match is None else match.target,
                    "" if match is None else match.target_rank,
                    "" if match is None else " ".join(t.value for t in match.techniques),
                ]
            )


def _cell(value: float | None) -> str:
    return "—" if value is None else svg_charts.percent(value)


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return [*lines, ""]


def _code(name: str) -> str:
    return f"`{name}`"


def render_summary(
    results: Mapping[str, Sequence[Metrics]],
    scored: Sequence[Scored],
    thresholds: SeverityThresholds,
    popular: Mapping[Ecosystem, frozenset[str]],
) -> str:
    """Tablas en Markdown para el informe. Se regeneran con cada medición."""
    lines = [
        "# Medición del detector de typosquatting: tablas generadas",
        "",
        "Archivo generado por `eval/typosquatting/evaluate.py`; no lo edites a mano.",
        "El análisis está en [`../typosquatting.md`](../typosquatting.md).",
        "",
    ]
    lines += _composition(scored)
    lines += _at_configured(results, thresholds)
    lines += _best(results, thresholds)
    lines += _scope(results, scored, thresholds.high)
    lines += _misses(scored, popular, thresholds.high)
    lines += _techniques(scored, thresholds.high)
    lines += _examples(scored, thresholds.high)
    return "\n".join(lines).rstrip("\n") + "\n"


def _misses(
    scored: Sequence[Scored], popular: Mapping[Ecosystem, frozenset[str]], block: float
) -> list[str]:
    counts: Counter[tuple[Miss, Ecosystem]] = Counter()
    examples: dict[Miss, list[str]] = {}
    for item in scored:
        sample = item.sample
        if sample.group is not Group.TYPOSQUAT or sample.target is None or item.flagged(block):
            continue
        reason = miss_reason(
            sample.name, sample.target, popular[sample.ecosystem], scored=item.match is not None
        )
        counts[reason, sample.ecosystem] += 1
        shown = examples.setdefault(reason, [])
        if len(shown) < MISS_EXAMPLES:
            shown.append(f"{_code(sample.name)} → {_code(sample.target)}")
    rows = [
        [
            MISS_LABELS[reason],
            *(str(counts[reason, ecosystem]) for ecosystem in Ecosystem),
            ", ".join(examples.get(reason, [])),
        ]
        for reason in Miss
    ]
    return [
        f"## Por qué se escapan los typosquats (umbral {svg_charts.decimal(block)})",
        "",
        *_table(["Forma del nombre", "npm", "PyPI", "Ejemplos"], rows),
    ]


def _composition(scored: Sequence[Scored]) -> list[str]:
    counts = Counter((item.sample.group, item.sample.ecosystem) for item in scored)
    rows = [
        [GROUP_LABELS[group], *(f"{counts[group, eco]:,}".replace(",", " ") for eco in Ecosystem)]
        for group in Group
    ]
    return ["## Dataset", "", *_table(["Grupo", "npm", "PyPI"], rows)]


def _metric_row(label: str, row: Metrics) -> list[str]:
    return [
        label,
        _cell(row.precision),
        _cell(row.recall),
        _cell(row.f1),
        _cell(row.in_scope.rate),
        *(_cell(row.by_group[group].rate) for group in NEGATIVE_GROUPS),
    ]


_METRIC_HEADER = [
    "Umbral",
    "Precisión",
    "Recall",
    "F1",
    "Recall en alcance",
    "FP lista popular",
    "FP siguientes",
    "FP aleatorios",
]


def _at_configured(
    results: Mapping[str, Sequence[Metrics]], thresholds: SeverityThresholds
) -> list[str]:
    levels = [
        (thresholds.medium, "`medium`"),
        (thresholds.high, "`high`, bloquea"),
        (thresholds.critical, "`critical`"),
    ]
    lines = [
        "## Métricas en los umbrales configurados",
        "",
        "Con la política por defecto, el detector bloquea desde `high`. FP es la proporción",
        "de cada grupo de legítimos que se bloquearía con ese umbral.",
        "",
    ]
    for scope, rows in results.items():
        lines += [f"### {_scope_label(scope)}", ""]
        lines += _table(
            _METRIC_HEADER,
            (
                _metric_row(f"{svg_charts.decimal(level)} · {name}", at(rows, level))
                for level, name in levels
            ),
        )
    return lines


def _scope_label(scope: str) -> str:
    return {"npm": "npm", "pypi": "PyPI", ALL: "Ambos ecosistemas"}[scope]


def _best(results: Mapping[str, Sequence[Metrics]], thresholds: SeverityThresholds) -> list[str]:
    rows = []
    for scope, metrics in results.items():
        best = best_f1(metrics)
        current = at(metrics, thresholds.high)
        rows.append(
            [
                _scope_label(scope),
                svg_charts.decimal(best.threshold),
                _cell(best.f1),
                _cell(best.precision),
                _cell(best.recall),
                _cell(current.f1),
            ]
        )
    header = ["Alcance", "Umbral con mejor F1", "F1", "Precisión", "Recall", "F1 con el actual"]
    return ["## Umbral con mejor F1", "", *_table(header, rows)]


def _scope(
    results: Mapping[str, Sequence[Metrics]], scored: Sequence[Scored], block: float
) -> list[str]:
    rows = []
    for ecosystem in Ecosystem:
        metrics = at(results[ecosystem.value], block)
        positives = [
            item
            for item in scored
            if item.sample.ecosystem is ecosystem and item.sample.group is Group.TYPOSQUAT
        ]
        out_total = len(positives) - metrics.in_scope.total
        out_flagged = metrics.tp - metrics.in_scope.flagged
        agreeing = sum(
            item.flagged(block)
            and item.match is not None
            and item.match.target == item.sample.target
            for item in positives
        )
        rows.append(
            [
                ecosystem.value if ecosystem is Ecosystem.NPM else "PyPI",
                f"{metrics.in_scope.flagged} de {metrics.in_scope.total}",
                f"{out_flagged} de {out_total}",
                f"{agreeing} de {metrics.tp}",
            ]
        )
    header = [
        "Ecosistema",
        "Bloqueados con el objetivo en la lista",
        "Bloqueados con el objetivo fuera de la lista",
        "Bloqueados con el objetivo correcto",
    ]
    return [
        f"## Alcance de la lista popular (umbral {svg_charts.decimal(block)})",
        "",
        *_table(header, rows),
    ]


def _techniques(scored: Sequence[Scored], block: float) -> list[str]:
    counts: Counter[tuple[str, str]] = Counter()
    for item in scored:
        if item.match is None or not item.flagged(block):
            continue
        group = item.sample.group
        if group is Group.TYPOSQUAT_UNTARGETED:
            continue
        counts[item.match.techniques[0].value, group.value] += 1
    techniques = sorted({technique for technique, _ in counts})
    groups = (Group.TYPOSQUAT, Group.NEXT, Group.RANDOM)
    rows = [
        [_code(technique), *(str(counts[technique, group.value]) for group in groups)]
        for technique in techniques
    ]
    header = ["Técnica más fuerte", "Typosquats", "FP siguientes", "FP aleatorios"]
    return [
        f"## Técnica que decide el bloqueo (umbral {svg_charts.decimal(block)})",
        "",
        "Ambos ecosistemas juntos.",
        "",
        *_table(header, rows),
    ]


def _examples(scored: Sequence[Scored], block: float) -> list[str]:
    lines = [f"## Ejemplos (umbral {svg_charts.decimal(block)})", ""]
    for ecosystem in Ecosystem:
        items = [item for item in scored if item.sample.ecosystem is ecosystem]
        false_positives = sorted(
            (
                item
                for item in items
                if item.sample.group is Group.NEXT and item.flagged(block) and item.match
            ),
            key=lambda item: (-item.score, item.sample.rank or 0),
        )[:EXAMPLES]
        missed = [
            item
            for item in items
            if item.sample.group is Group.TYPOSQUAT and not item.flagged(block)
        ][:EXAMPLES]
        label = "npm" if ecosystem is Ecosystem.NPM else "PyPI"
        lines += [f"### {label}: legítimos bloqueados (siguientes del ranking)", ""]
        lines += _table(
            ["Paquete", "Puesto", "Se parece a", "Puntaje", "Técnicas"],
            (
                [
                    _code(item.sample.name),
                    str(item.sample.rank),
                    _code(item.match.target),
                    svg_charts.decimal(item.score),
                    ", ".join(t.value for t in item.match.techniques),
                ]
                for item in false_positives
                if item.match is not None
            ),
        )
        lines += [f"### {label}: typosquats que no se bloquean", ""]
        lines += _table(
            ["Paquete", "Imita a", "Puntaje"],
            (
                [_code(item.sample.name), _code(item.sample.target or ""), _cell_score(item)]
                for item in missed
            ),
        )
    return lines


def _cell_score(item: Scored) -> str:
    return "—" if item.match is None else svg_charts.decimal(item.score)


def threshold_chart(rows: Sequence[Metrics], label: str, block: float) -> svg_charts.Chart:
    """Precisión, recall y F1 según el umbral de bloqueo."""
    visible = [row for row in rows if CHART_RANGE[0] <= row.threshold <= CHART_RANGE[1]]
    series = [
        svg_charts.Series("Precisión", [(row.threshold, row.precision) for row in visible]),
        svg_charts.Series("Recall", [(row.threshold, row.recall) for row in visible]),
        svg_charts.Series("F1", [(row.threshold, row.f1) for row in visible]),
    ]
    positives = rows[0].tp + rows[0].fn
    negatives = rows[0].fp + rows[0].tn
    return svg_charts.Chart(
        title=f"{label}: precisión, recall y F1 según el umbral de bloqueo",
        subtitle=f"{positives} typosquats reales y {negatives:,} paquetes legítimos".replace(
            ",", " "
        ),
        description=(
            f"Curvas escalonadas de precisión, recall y F1 del detector de typosquatting en "
            f"{label} para umbrales de {svg_charts.decimal(CHART_RANGE[0])} a "
            f"{svg_charts.decimal(CHART_RANGE[1])}. Los valores están en metricas.csv."
        ),
        series=series,
        x_range=CHART_RANGE,
        x_ticks=CHART_TICKS,
        x_label="Umbral de bloqueo",
        x_format=svg_charts.decimal,
        y_max=1.0,
        y_format=svg_charts.percent,
        marker=svg_charts.Marker(block, f"bloqueo actual: {svg_charts.decimal(block)}"),
    )


def false_positive_chart(rows: Sequence[Metrics], label: str, block: float) -> svg_charts.Chart:
    """Proporción de legítimos bloqueados según el umbral, por grupo de legítimos."""
    visible = [row for row in rows if CHART_RANGE[0] <= row.threshold <= CHART_RANGE[1]]
    groups = (Group.NEXT, Group.RANDOM)
    peak = max((row.by_group[group].rate or 0.0) for row in visible for group in groups)
    y_max, _ = svg_charts.nice_axis_max(peak)
    titles = {Group.NEXT: "Siguientes del ranking", Group.RANDOM: "Muestra aleatoria"}
    totals = {group: rows[0].by_group[group].total for group in groups}
    return svg_charts.Chart(
        title=f"{label}: legítimos bloqueados según el umbral",
        subtitle=(
            f"{totals[Group.NEXT]:,} siguientes del ranking y "
            f"{totals[Group.RANDOM]:,} al azar del registro"
        ).replace(",", " "),
        description=(
            f"Tasa de falsos positivos del detector de typosquatting en {label} para cada "
            "grupo de paquetes legítimos según el umbral de bloqueo. La lista popular no "
            "aparece porque nunca se marca. Los valores están en metricas.csv."
        ),
        series=[
            svg_charts.Series(
                titles[group], [(row.threshold, row.by_group[group].rate) for row in visible]
            )
            for group in groups
        ],
        x_range=CHART_RANGE,
        x_ticks=CHART_TICKS,
        x_label="Umbral de bloqueo",
        x_format=svg_charts.decimal,
        y_max=y_max,
        y_format=svg_charts.percent,
        marker=svg_charts.Marker(block, f"bloqueo actual: {svg_charts.decimal(block)}"),
    )


def _write(path: Path, text: str) -> None:
    # LF y salto final para que el archivo pase los hooks de pre-commit en cualquier SO.
    path.write_text(text, encoding="utf-8", newline="\n")


def write_results(
    output_dir: Path,
    results: Mapping[str, Sequence[Metrics]],
    scored: Sequence[Scored],
    thresholds: SeverityThresholds,
    popular: Mapping[Ecosystem, frozenset[str]],
) -> None:
    """Escribe las tablas, el detalle y las gráficas en ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_metrics_csv(output_dir / "metricas.csv", results)
    write_detail_csv(output_dir / "detalle.csv", scored, thresholds.report)
    _write(output_dir / "resumen.md", render_summary(results, scored, thresholds, popular))
    for ecosystem in Ecosystem:
        label = "npm" if ecosystem is Ecosystem.NPM else "PyPI"
        rows = results[ecosystem.value]
        _write(
            output_dir / f"umbral-{ecosystem.value}.svg",
            svg_charts.render(threshold_chart(rows, label, thresholds.high)),
        )
        _write(
            output_dir / f"falsos-positivos-{ecosystem.value}.svg",
            svg_charts.render(false_positive_chart(rows, label, thresholds.high)),
        )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Lee los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--popular-dir",
        type=Path,
        default=None,
        help="popular lists to compare against (default: SUPPLY_SHIELD_POPULAR_PACKAGES_DIR)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="typosquatting allowlist (default: SUPPLY_SHIELD_TYPOSQUATTING_ALLOWLIST_FILE)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Puntúa el dataset y escribe los resultados. Devuelve el código de salida."""
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    thresholds = SeverityThresholds(
        report=settings.typosquatting_report_threshold,
        medium=settings.typosquatting_medium_threshold,
        high=settings.typosquatting_high_threshold,
        critical=settings.typosquatting_critical_threshold,
    )
    try:
        analyzer = TyposquattingAnalyzer.from_files(
            args.popular_dir or settings.popular_packages_dir,
            args.allowlist or settings.typosquatting_allowlist_file,
            thresholds=thresholds,
        )
        samples = [
            sample
            for ecosystem in Ecosystem
            for sample in [
                *load_dataset(args.dataset_dir, ecosystem),
                *popular_samples(analyzer, ecosystem),
            ]
        ]
    # ValidationError, AllowlistLoadError y UnicodeDecodeError son subclases de ValueError.
    except (OSError, KeyError, ValueError) as exc:
        log.error("typosquatting_eval_failed", error=str(exc), error_type=type(exc).__name__)
        return 1
    started = time.perf_counter()
    scored = score_samples(samples, analyzer)
    elapsed = time.perf_counter() - started
    popular = {ecosystem: frozenset(index.names) for ecosystem, index in analyzer.indexes.items()}
    results = sweep(scored, popular)
    write_results(args.output_dir, results, scored, thresholds, popular)
    block = at(results[ALL], thresholds.high)
    log.info(
        "typosquatting_eval_done",
        names=len(scored),
        seconds=round(elapsed, 1),
        threshold=thresholds.high,
        precision=_ratio(block.precision),
        recall=_ratio(block.recall),
        f1=_ratio(block.f1),
        output_dir=str(args.output_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
