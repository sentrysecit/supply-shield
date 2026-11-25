"""Medición del detector de typosquatting (D4-6): métricas, clasificación de errores y gráficas."""

import csv
from pathlib import Path
from xml.etree import ElementTree

import pytest

import evaluate
import svg_charts
from build_dataset import EcosystemDataset, TyposquatRow, write_dataset
from evaluate import Group, Metrics, Miss, Rates, Sample, Scored, best_f1, measure, miss_reason
from supply_shield.analyzers.typosquatting import (
    PopularPackages,
    Technique,
    TyposquatMatch,
)
from supply_shield.core.enums import Ecosystem

NPM = Ecosystem.NPM
PYPI = Ecosystem.PYPI
POPULAR = {NPM: frozenset({"cross-env", "express"}), PYPI: frozenset({"requests"})}
SVG = "{http://www.w3.org/2000/svg}"


def _parse_svg(text: str) -> ElementTree.Element:
    return ElementTree.fromstring(text)  # noqa: S314 - SVG que acaba de generar la prueba


def _match(name: str, target: str, score: float) -> TyposquatMatch:
    return TyposquatMatch(
        name=name,
        target=target,
        target_rank=1,
        score=score,
        techniques=(Technique.EDIT_DISTANCE,),
    )


def _scored() -> list[Scored]:
    return [
        Scored(
            Sample(NPM, Group.TYPOSQUAT, "crossenv", target="cross-env"),
            _match("crossenv", "cross-env", 0.95),
        ),
        Scored(Sample(NPM, Group.TYPOSQUAT, "express-fork", target="express"), None),
        Scored(Sample(NPM, Group.TYPOSQUAT, "stripe-x", target="stripe"), None),
        Scored(Sample(NPM, Group.TYPOSQUAT_UNTARGETED, "abc"), _match("abc", "abd", 0.9)),
        Scored(
            Sample(NPM, Group.NEXT, "babel-traverse", rank=5080),
            _match("babel-traverse", "@babel/traverse", 1.0),
        ),
        Scored(Sample(NPM, Group.NEXT, "left-pad", rank=6000), None),
        Scored(Sample(NPM, Group.RANDOM, "zzz"), None),
        Scored(Sample(NPM, Group.POPULAR, "express", rank=1), None),
    ]


def test_measure_counts_blocks_and_ignores_untargeted_typosquats() -> None:
    metrics = measure(_scored(), 0.85, scope="npm", popular=POPULAR)

    assert (metrics.tp, metrics.fn, metrics.fp, metrics.tn) == (1, 2, 1, 3)
    assert metrics.precision == 0.5
    assert metrics.recall == pytest.approx(1 / 3)
    assert metrics.f1 == pytest.approx(0.4)
    assert metrics.in_scope == Rates(1, 2)
    assert metrics.by_group[Group.NEXT] == Rates(1, 2)
    assert metrics.by_group[Group.RANDOM].rate == 0
    assert metrics.by_group[Group.TYPOSQUAT_UNTARGETED] == Rates(1, 1)


def test_metrics_without_blocks_or_without_items_are_undefined() -> None:
    none_blocked = measure(_scored(), 1.0, scope="npm", popular=POPULAR)
    empty = measure([], 0.85, scope="npm", popular=POPULAR)

    assert none_blocked.precision == 0.0
    assert none_blocked.recall == 0.0
    assert none_blocked.f1 is None
    assert (empty.precision, empty.recall, empty.f1) == (None, None, None)
    assert empty.in_scope.rate is None


def test_best_f1_prefers_the_higher_threshold_on_a_tie() -> None:
    rows = [measure(_scored(), threshold, scope="npm", popular=POPULAR) for threshold in (0.8, 0.9)]

    assert best_f1(rows).threshold == 0.9
    assert evaluate.at(rows, 0.8).threshold == 0.8
    with pytest.raises(ValueError, match="was not measured"):
        evaluate.at(rows, 0.85)


def test_sweep_measures_each_ecosystem_and_both_together() -> None:
    scored = [*_scored(), Scored(Sample(PYPI, Group.RANDOM, "x"), None)]

    results = evaluate.sweep(scored, POPULAR)

    assert set(results) == {"npm", "pypi", "all"}
    assert [row.threshold for row in results["all"]] == list(evaluate.THRESHOLDS)
    assert evaluate.at(results["pypi"], 0.85).tn == 1
    assert evaluate.at(results["all"], 0.85).tn == 4


@pytest.mark.parametrize(
    ("name", "target", "scored", "reason"),
    [
        ("stripe-x", "stripe", False, Miss.OUT_OF_LIST),
        ("@dervix/ws", "ws", False, Miss.SCOPE),
        ("@prime0/inimatch", "minimatch", False, Miss.SCOPE),
        ("chalk-core", "chalk", True, Miss.COMBO),
        ("react-dom-helpers", "react-dom", False, Miss.COMBO),
        ("typescipt-cli", "typescript", False, Miss.COMBO_TYPO),
        ("axiosqqq", "axios", True, Miss.BELOW_THRESHOLD),
        ("lodahsjs", "lodash", False, Miss.OTHER),
    ],
)
def test_miss_reason_describes_the_shape_of_the_name(
    name: str, target: str, *, scored: bool, reason: Miss
) -> None:
    popular = frozenset({"ws", "minimatch", "chalk", "react-dom", "typescript", "axios", "lodash"})

    assert miss_reason(name, target, popular, scored=scored) is reason


def _write_popular(directory: Path, ecosystem: Ecosystem, names: tuple[str, ...]) -> None:
    moment = "2026-11-01T00:00:00Z"
    popular = PopularPackages.model_validate(
        {
            "ecosystem": ecosystem.value,
            "generated_at": moment,
            "source": {"name": "t", "url": "https://t", "ranking": "t", "published_at": moment},
            "packages": names,
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{ecosystem.value}.json").write_text(popular.model_dump_json(), encoding="utf-8")


def _environment(tmp_path: Path) -> list[str]:
    popular = tmp_path / "popular"
    _write_popular(popular, NPM, ("cross-env", "express", "lodash"))
    _write_popular(popular, PYPI, ("requests", "flask"))
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("version: 1\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    write_dataset(
        dataset,
        NPM,
        EcosystemDataset(
            typosquats=[
                TyposquatRow("crossenv", "cross-env", ("MAL-1",)),
                TyposquatRow("express-fork", "express", ("MAL-2",)),
                TyposquatRow("abc123", None, ("MAL-3",)),
            ],
            next=[(5001, "cross_env"), (5002, "left-pad")],
            random=["zzz-unique"],
            unreviewed=[],
            overlaps={},
        ),
    )
    write_dataset(
        dataset,
        PYPI,
        EcosystemDataset(
            typosquats=[TyposquatRow("reqursts", "requests", ("MAL-4",))],
            next=[(5001, "django")],
            random=["some-random-name"],
            unreviewed=[],
            overlaps={},
        ),
    )
    return [
        *("--dataset-dir", str(dataset), "--output-dir", str(tmp_path / "results")),
        *("--popular-dir", str(popular), "--allowlist", str(allowlist)),
    ]


def test_main_writes_metrics_detail_summary_and_charts(tmp_path: Path) -> None:
    assert evaluate.main(_environment(tmp_path)) == 0

    results = tmp_path / "results"
    with (results / "metricas.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3 * len(evaluate.THRESHOLDS)
    block = next(row for row in rows if row["scope"] == "npm" and row["threshold"] == "0.85")
    # crossenv se bloquea; express-fork (combosquat) no; cross_env (legítimo) sí.
    assert (block["tp"], block["fn"], block["fp"]) == ("1", "1", "1")
    assert block["fpr_popular"] == "0.00000"
    with (results / "detalle.csv").open(encoding="utf-8", newline="") as handle:
        detail = {row["name"]: row for row in csv.DictReader(handle)}
    assert detail["crossenv"]["matched_target"] == "cross-env"
    assert "left-pad" not in detail
    summary = (results / "resumen.md").read_text(encoding="utf-8")
    assert "## Por qué se escapan los typosquats" in summary
    assert "Nombre imitado más otras palabras" in summary
    for chart in ("umbral-npm", "umbral-pypi", "falsos-positivos-npm", "falsos-positivos-pypi"):
        root = _parse_svg((results / f"{chart}.svg").read_text(encoding="utf-8"))
        assert root.tag == f"{SVG}svg"


def test_main_fails_when_the_dataset_is_missing(tmp_path: Path) -> None:
    arguments = _environment(tmp_path)
    arguments[1] = str(tmp_path / "nowhere")

    assert evaluate.main(arguments) == 1
    assert not (tmp_path / "results").exists()


def test_metric_properties_on_a_hand_built_row() -> None:
    row = Metrics("npm", 0.85, tp=3, fn=1, fp=1, tn=95, in_scope=Rates(3, 3), by_group={})

    assert row.precision == 0.75
    assert row.recall == 0.75
    assert row.f1 == pytest.approx(0.75)


@pytest.mark.parametrize(
    ("value", "text"), [(0.0, "0 %"), (0.015, "1,5 %"), (0.05, "5 %"), (0.31, "31 %")]
)
def test_percent_uses_a_decimal_comma(value: float, text: str) -> None:
    assert svg_charts.percent(value) == text


def test_axis_max_is_a_round_step_multiple() -> None:
    assert svg_charts.nice_axis_max(0.17) == (0.2, 0.05)
    assert svg_charts.nice_axis_max(1.0) == (1.0, 0.2)
    assert svg_charts.nice_axis_max(0.0) == (0.005, 0.005)
    assert svg_charts.decimal(0.85) == "0,85"


def test_value_at_follows_the_step_curve() -> None:
    series = svg_charts.Series("s", [(0.7, 0.1), (0.8, 0.2), (0.9, None)])

    assert svg_charts.value_at(series, 0.85) == 0.2
    assert svg_charts.value_at(series, 0.95) is None
    assert svg_charts.value_at(series, 0.6) is None


def _chart(*series: svg_charts.Series) -> svg_charts.Chart:
    return svg_charts.Chart(
        title="Título & <prueba>",
        subtitle="sub",
        description="desc",
        series=series,
        x_range=(0.7, 1.0),
        x_ticks=(0.7, 0.85, 1.0),
        x_label="Umbral",
        x_format=svg_charts.decimal,
        y_max=1.0,
        y_format=svg_charts.percent,
        marker=svg_charts.Marker(0.85, "actual"),
    )


def test_render_is_valid_svg_with_a_gap_for_undefined_values_and_legend_values() -> None:
    curve = svg_charts.Series("Recall", [(0.7, 0.6), (0.8, None), (0.85, 0.5), (1.0, 0.1)])

    svg = svg_charts.render(_chart(curve))

    root = _parse_svg(svg)
    assert len(root.findall(f"{SVG}path")) == 2
    texts = [element.text for element in root.iter(f"{SVG}text")]
    assert "Recall 50 %" in texts
    assert "Título & <prueba>" in texts
    assert svg.endswith("</svg>\n")


def test_render_rejects_more_series_than_validated_colors() -> None:
    series = [svg_charts.Series(str(i), [(0.7, 0.1)]) for i in range(4)]

    with pytest.raises(ValueError, match="at most 3 series"):
        svg_charts.render(_chart(*series))


def test_popular_samples_never_count_as_false_positives(tmp_path: Path) -> None:
    arguments = _environment(tmp_path)
    assert evaluate.main(arguments) == 0

    with (tmp_path / "results" / "metricas.csv").open(encoding="utf-8", newline="") as handle:
        rates = {row["fpr_popular"] for row in csv.DictReader(handle)}

    assert rates == {"0.00000"}


def test_labels_cover_every_group() -> None:
    assert set(evaluate.GROUP_LABELS) == set(Group)
    assert set(evaluate.MISS_LABELS) == set(Miss)
