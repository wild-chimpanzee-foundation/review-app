from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import ui


@dataclass
class MapMarker:
    lat: float
    lon: float
    label: str = field(default="")


def render_location_map(markers: list[MapMarker], height: str = "400px") -> None:
    """Render a Leaflet map with markers, auto-fitted to show all points."""
    if not markers:
        return

    m = (
        ui.leaflet(center=(markers[0].lat, markers[0].lon), zoom=10)
        .classes("w-full")
        .style(f"height:{height}")
    )

    for mk in markers:
        marker = m.marker(latlng=(mk.lat, mk.lon))
        if mk.label:
            marker.run_method("bindTooltip", mk.label)

    if len(markers) > 1:
        lats = [mk.lat for mk in markers]
        lons = [mk.lon for mk in markers]
        m.run_map_method(
            "fitBounds", [[min(lats), min(lons)], [max(lats), max(lons)]], {"padding": [30, 30]}
        )
