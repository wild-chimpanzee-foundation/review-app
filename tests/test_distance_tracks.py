"""Distance graph geometry and sample-state rendering."""

import json
import shutil
import subprocess
from math import ceil
from pathlib import Path

import pytest
from review_app.app.pages.distance_review.tracks import (
    GRID_BOTTOM,
    GRID_TOP,
    STATUS_BAND_FRACTION,
    track_options,
)


def chart_height(individual_count):
    return 295 + max(28, individual_count * 32)


def test_tracks_keep_measured_distances_and_review_states_in_separate_coordinates():
    individuals = [{"id": "one", "individual_number": 1}, {"id": "two", "individual_number": 2}]
    samples = [
        {"individual_id": "one", "offset_ms": 1000, "distance_m": 5},
        {"individual_id": "one", "offset_ms": 3000, "skip_reason": "midpoint not visible"},
        {"individual_id": "one", "offset_ms": 5000, "skip_reason": "individual absent"},
        {"individual_id": "two", "offset_ms": 1000, "distance_m": 8},
        {"individual_id": "two", "offset_ms": 5000, "distance_m": 9},
    ]

    height = chart_height(len(individuals))
    options = track_options(individuals, samples, [1000, 3000, 5000], 6, "one", 3000, height)
    active, active_intervals, active_unreviewed, other, other_intervals, other_unreviewed = (
        options["series"]
    )

    assert active["data"] == [[1, 5], [3, None], [5, None]]
    assert other["data"] == [[1, 8], [3, None], [5, 9]]
    assert not active["connectNulls"] and not other["connectNulls"]
    assert active["lineStyle"]["color"] == "#f59e0b"
    assert other["lineStyle"]["color"] == "#94a3b8"
    assert [active["id"], active_intervals["id"], active_unreviewed["id"]] == [
        "one",
        "one-intervals",
        "one-unreviewed",
    ]
    assert active["markLine"]["data"] == [{"xAxis": 3}]

    # Skip intervals occupy a separate, proportional status band, not meter values.
    lane_one, lane_two = [row["value"][1] for row in active_intervals["data"]]
    assert lane_one == lane_two < 0
    assert [row["value"][0::2] + [row["value"][3]] for row in active_intervals["data"]] == [
        [2, 4, 1],
        [4, 6, 0],
    ]
    assert [row["statusLabel"] for row in active_intervals["data"]] == [
        "Not visible",
        "Absent",
    ]
    assert active_unreviewed["data"] == []
    assert other_intervals["data"] == []
    assert other_unreviewed["data"][0]["value"][0] == 3
    assert other_unreviewed["symbol"] == "emptyCircle"
    assert options["graphic"]["elements"][0]["style"]["text"] == "#1"
    assert options["graphic"]["elements"][1]["style"]["text"] == "#2"


def test_status_band_pixel_geometry_stays_readable_for_large_and_fractional_distances():
    for max_distance in (100.25, 500.75):
        individuals = [{"id": f"id-{i}", "individual_number": i + 1} for i in range(10)]
        samples = [
            {"individual_id": individuals[0]["id"], "offset_ms": 0, "distance_m": max_distance}
        ]
        height = chart_height(len(individuals))
        options = track_options(
            individuals, samples, [0], 1, individuals[0]["id"], chart_height=height
        )
        plot_height = height - GRID_TOP - GRID_BOTTOM
        lane_pixel_gap = plot_height * STATUS_BAND_FRACTION / len(individuals)
        lane_label_tops = [element["top"] for element in options["graphic"]["elements"]]

        assert options["grid"] == {
            "left": 62,
            "right": 18,
            "top": GRID_TOP,
            "bottom": GRID_BOTTOM,
        }
        assert lane_pixel_gap >= 12
        assert all(
            b - a == pytest.approx(lane_pixel_gap)
            for a, b in zip(lane_label_tops, lane_label_tops[1:])
        )
        assert options["yAxis"]["max"] == ceil(max_distance * 1.1)
        assert options["yAxis"]["min"] == pytest.approx(
            -options["yAxis"]["max"] * STATUS_BAND_FRACTION / (1 - STATUS_BAND_FRACTION)
        )
        assert options["yAxis"]["axisLabel"][":formatter"] == (
            "value => value < 0 ? '' : `${Math.round(value)} m`"
        )
        assert ":formatter" not in options["yAxis"]
        assert "interval" not in options["yAxis"]
        assert options["series"][1]["data"] == []
        assert "height = Math.abs(api.size([0, " in options["series"][1][":renderItem"]
        assert "* 0.2;" in options["series"][1][":renderItem"]

    # The page's height scaling also keeps 20 rows apart at a usable pixel scale.
    many = [{"id": f"id-{i}", "individual_number": i + 1} for i in range(20)]
    tall = chart_height(len(many))
    many_options = track_options(many, [], [], 2, None, chart_height=tall)
    many_gap = (tall - GRID_TOP - GRID_BOTTOM) * STATUS_BAND_FRACTION / len(many)
    assert many_gap > 10
    assert len(many_options["graphic"]["elements"]) == 20


def test_each_individual_status_lane_aligns_with_its_own_label_at_multiple_scales():
    individuals = [{"id": f"animal-{index}", "individual_number": index + 1} for index in range(4)]
    height = chart_height(len(individuals))
    plot_height = height - GRID_TOP - GRID_BOTTOM

    for distance in (2.5, 100.25, 500.75):
        samples = [
            {
                "individual_id": individual["id"],
                "offset_ms": 1000,
                "skip_reason": "midpoint not visible",
            }
            for individual in individuals
        ]
        samples.append(
            {"individual_id": individuals[0]["id"], "offset_ms": 3000, "distance_m": distance}
        )
        options = track_options(
            individuals,
            samples,
            [1000, 3000],
            4,
            individuals[0]["id"],
            chart_height=height,
        )
        y_min = options["yAxis"]["min"]
        y_max = options["yAxis"]["max"]
        for lane, individual in enumerate(individuals):
            interval_y = options["series"][lane * 3 + 1]["data"][0]["value"][1]
            interval_screen_y = GRID_TOP + (y_max - interval_y) / (y_max - y_min) * plot_height
            label = options["graphic"]["elements"][lane]
            label_screen_y = label["top"] + label["style"]["height"] / 2
            assert label["style"]["text"] == f"#{individual['individual_number']}"
            assert interval_screen_y == pytest.approx(label_screen_y)
            if lane:
                previous_y = options["series"][(lane - 1) * 3 + 1]["data"][0]["value"][1]
                assert interval_y < previous_y


def test_graph_uses_one_shared_elapsed_time_geometry_and_empty_short_clip_is_valid():
    individuals = [{"id": "one", "individual_number": 3}, {"id": "two", "individual_number": 4}]
    height = chart_height(len(individuals))
    options = track_options(
        individuals,
        [{"individual_id": "one", "offset_ms": 250, "distance_m": 2.5}],
        [250, 1250],
        1.5,
        "one",
        chart_height=height,
    )

    assert isinstance(options["grid"], dict)
    assert options["xAxis"]["min"] == 0
    assert options["xAxis"]["max"] == 1.5
    assert options["xAxis"]["name"] == "Time (s)"
    assert options["yAxis"]["name"] == "Distance (m) / status lanes"
    assert options["series"][0]["data"] == [[0.25, 2.5], [1.25, None]]
    assert options["series"][2]["data"][0]["value"][0] == 1.25
    assert options["series"][5]["data"][0]["value"][0] == 0.25

    empty = track_options([], [], [], 0, None, chart_height=chart_height(0))
    assert empty["grid"] == options["grid"]
    assert empty["xAxis"]["max"] > 0
    assert empty["series"] == []


def test_skip_intervals_stay_within_clip_and_keep_exact_elapsed_time():
    options = track_options(
        [{"id": "one", "individual_number": 1}],
        [
            {"individual_id": "one", "offset_ms": 1000, "skip_reason": "midpoint not visible"},
            {"individual_id": "one", "offset_ms": 3000, "skip_reason": "individual absent"},
        ],
        [1000, 3000],
        2.5,
        "one",
    )
    intervals = options["series"][1]["data"]
    assert [row["value"][0] for row in intervals] == [0, 2]
    assert [row["value"][2:] for row in intervals] == [[2, 1], [2.5, 0]]


def test_installed_nicegui_dynamic_property_conversion_installs_axis_formatter():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise NiceGUI dynamic-property conversion")
    import nicegui

    converter = Path(nicegui.__file__).parent / "static/utils/dynamic_properties.js"
    options = track_options(
        [{"id": "one", "individual_number": 1}],
        [],
        [],
        2,
        None,
    )
    script = """
        import fs from 'node:fs';
        import assert from 'node:assert/strict';
        const source = fs.readFileSync(process.argv[1], 'utf8');
        const {convertDynamicProperties} = await import(
            'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
        );
        let input = '';
        for await (const chunk of process.stdin) input += chunk;
        const options = JSON.parse(input);
        convertDynamicProperties(options, true);
        assert.equal(typeof options.yAxis.axisLabel.formatter, 'function');
        assert.equal(Object.hasOwn(options.yAxis, 'axisLabel.formatter'), false);
        const format = options.yAxis.axisLabel.formatter;
        assert.equal(format(-100), '');
        assert.equal(format(-0.1), '');
        assert.equal(format(0), '0 m');
        assert.equal(format(6.6), '7 m');
        assert.equal(format(12.5), '13 m');
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(converter)],
        input=json.dumps(options),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
