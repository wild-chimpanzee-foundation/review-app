"""Distance samples and review states in one elapsed-time graph."""

from math import ceil

from review_app.app.translations import t

GRID_TOP = 42
GRID_BOTTOM = 52
STATUS_BAND_FRACTION = 0.25


def track_options(
    individuals, samples, offsets, duration, active_id, current_offset=None, chart_height=500
) -> dict:
    """Build one chart with meter-scaled distances and a proportional status band."""
    by_sample = {(row["individual_id"], row["offset_ms"]): row for row in samples}
    labels = [f"#{row['individual_number']}" for row in individuals]
    count = len(individuals)
    series = []
    max_distance = max(
        (row["distance_m"] for row in samples if row.get("distance_m") is not None),
        default=0,
    )
    y_max = max(1, ceil(max_distance * 1.1))
    band_bottom = y_max * STATUS_BAND_FRACTION / (1 - STATUS_BAND_FRACTION)
    lane_span = band_bottom / count if count else 0
    clip_duration = max(0, duration)
    plot_height = max(0, chart_height - GRID_TOP - GRID_BOTTOM)
    lane_pixel_height = plot_height * STATUS_BAND_FRACTION / count if count else 0
    graphics = []

    for lane, individual in enumerate(individuals):
        individual_id = individual["id"]
        lane_y = -lane_span * (lane + 0.5)
        active = individual_id == active_id
        color = "#f59e0b" if active else "#94a3b8"
        data = []
        intervals = []
        unreviewed = []
        for position, offset in enumerate(offsets):
            sample = by_sample.get((individual_id, offset))
            distance = sample.get("distance_m") if sample else None
            data.append([offset / 1000, distance])
            if sample is None:
                unreviewed.append(
                    {
                        "value": [offset / 1000, lane_y],
                        "statusLabel": t("distance_unreviewed"),
                    }
                )
            elif sample.get("skip_reason"):
                start = (offsets[position - 1] + offset) / 2000 if position else 0
                end = (
                    (offset + offsets[position + 1]) / 2000
                    if position + 1 < len(offsets)
                    else clip_duration
                )
                start = min(clip_duration, max(0, start))
                end = min(clip_duration, max(start, end))
                midpoint_hidden = sample["skip_reason"] == "midpoint not visible"
                intervals.append(
                    {
                        "value": [start, lane_y, end, int(midpoint_hidden)],
                        "sampleOffset": offset,
                        "statusLabel": t(
                            "distance_midpoint_hidden" if midpoint_hidden else "distance_absent"
                        ),
                    }
                )

        # Place individual labels in the reserved pixel band, independently of meter scale.
        lane_top = GRID_TOP + plot_height * (1 - STATUS_BAND_FRACTION)
        graphics.append(
            {
                "type": "text",
                "left": 4,
                "top": lane_top + lane_pixel_height * lane,
                "silent": True,
                "style": {
                    "text": labels[lane],
                    "width": 52,
                    "height": lane_pixel_height,
                    "overflow": "truncate",
                    "verticalAlign": "middle",
                    "fill": "#94a3b8",
                    "fontSize": 11,
                },
            }
        )
        line = {
            "id": str(individual_id),
            "name": labels[lane],
            "type": "line",
            "data": data,
            "connectNulls": False,
            "symbol": "circle",
            "symbolSize": 9 if active else 7,
            "lineStyle": {"color": color, "width": 3 if active else 1.5},
            "itemStyle": {"color": color},
            "z": 5 if active else 2,
        }
        if active and current_offset is not None:
            line["markLine"] = {
                "silent": True,
                "symbol": "none",
                "label": {"show": False},
                "lineStyle": {"color": color, "type": "dashed", "opacity": 0.5},
                "data": [{"xAxis": current_offset / 1000}],
            }
        series.append(line)
        series.append(
            {
                "id": f"{individual_id}-intervals",
                "name": labels[lane],
                "type": "custom",
                "data": intervals,
                "encode": {"x": [0, 2], "y": 1},
                "clip": True,
                "z": 3 if active else 1,
                ":renderItem": """(params, api) => {
                    const start = api.coord([api.value(0), api.value(1)]);
                    const end = api.coord([api.value(2), api.value(1)]);
                    const height = Math.abs(api.size([0, __LANE_SPAN__])[1]) * 0.2;
                    const hidden = Boolean(api.value(3));
                    return {type: 'rect', shape: {x: start[0], y: start[1] - height / 2,
                        width: Math.max(1, end[0] - start[0]), height},
                        style: {fill: hidden ? 'transparent' : '__COLOR__',
                            stroke: '__COLOR__', lineWidth: hidden ? 2 : 1,
                            lineDash: hidden ? [2, 3] : [], opacity: __OPACITY__}};
                }""".replace("__LANE_SPAN__", repr(lane_span))
                .replace("__COLOR__", color)
                .replace("__OPACITY__", "1" if active else "0.6"),
            }
        )
        series.append(
            {
                "id": f"{individual_id}-unreviewed",
                "name": labels[lane],
                "type": "scatter",
                "data": unreviewed,
                "symbol": "emptyCircle",
                "symbolSize": min(11, lane_pixel_height * 0.85),
                "itemStyle": {"color": "#94a3b8", "borderColor": "#94a3b8"},
                "z": 4,
            }
        )

    return {
        "animation": False,
        "legend": {"data": labels, "type": "scroll", "top": 0, "textStyle": {"color": "#94a3b8"}},
        "graphic": {"elements": graphics},
        "grid": {"left": 62, "right": 18, "top": GRID_TOP, "bottom": GRID_BOTTOM},
        "xAxis": {
            "type": "value",
            "min": 0,
            "max": max(clip_duration, 0.001),
            "axisLabel": {"color": "#94a3b8"},
            "splitLine": {"show": False},
            "name": t("distance_track_time"),
            "nameLocation": "middle",
            "nameGap": 28,
            "nameTextStyle": {"color": "#94a3b8"},
        },
        "yAxis": {
            "type": "value",
            "min": -band_bottom,
            "max": y_max,
            "name": t("distance_track_distance"),
            "nameTextStyle": {"color": "#94a3b8"},
            "axisLabel": {
                "color": "#94a3b8",
                ":formatter": "value => value < 0 ? '' : `${Math.round(value)} m`",
            },
            "splitLine": {"lineStyle": {"color": "rgba(128,128,128,.15)"}},
            "axisLine": {"show": True},
        },
        "tooltip": {
            "trigger": "item",
            "renderMode": "richText",
            ":formatter": "p => p.data.statusLabel "
            "? `${p.seriesName} · ${p.data.statusLabel}` + "
            "(p.value.length === 4 ? `\\n${p.value[0].toFixed(1)}–${p.value[2].toFixed(1)} s` "
            ": `\\n${p.value[0].toFixed(1)} s`) "
            ": `${p.seriesName}\\n${p.value[0].toFixed(1)} s · ${p.value[1]} m`",
        },
        "series": series,
    }
