"""Player callbacks belong to the element, not the lifetime of its browser tab."""

from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import helpers, ui
from nicegui.testing import User
from review_app.app.pages.distance_review import player as distance_player
from review_app.app.pages.review import video_player

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.mark.parametrize("kind", ["shared", "distance"])
async def test_player_deletion_releases_callbacks_without_growing_layout_listeners(
    user: User, monkeypatch, kind
):
    monkeypatch.setattr(ui, "run_javascript", Mock())
    monkeypatch.setattr(video_player, "get_playback_speed", lambda: "1.0x")
    monkeypatch.setattr(video_player, "is_muted", lambda: True)
    observed = {}

    @ui.page("/player-event-lifetime")
    def page():
        client = ui.context.client
        before = len(client.layout._event_listeners)
        for index in range(5):
            with ui.column() as parent:
                if kind == "shared":
                    player = video_player.render_custom_video_player(
                        "/first.mp4", 10, f"lifetime-{index}", autoplay=False
                    )
                    expected = 4
                else:
                    player = ui.video("/first.mp4")
                    distance_player.install_manual_pause_snap(player, lambda _: None)
                    distance_player.install_anchor_editor(player, lambda _: None)
                    expected = 2
                assert len(player._event_listeners) >= expected
            # Exercise both direct deletion and deletion by a refreshable parent.
            if index % 2:
                parent.delete()
            else:
                player.delete()
                parent.delete()
            assert not player._event_listeners
            assert player.id not in client.elements
        observed["listener_growth"] = len(client.layout._event_listeners) - before

    await user.open("/player-event-lifetime")
    assert observed["listener_growth"] == 0


async def test_source_switch_keeps_player_callbacks_and_dispatches_to_current_owner(
    user: User, monkeypatch
):
    scripts = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, **_: scripts.append(code))
    monkeypatch.setattr(video_player, "get_playback_speed", lambda: "1.0x")
    monkeypatch.setattr(video_player, "is_muted", lambda: True)
    speed = Mock()
    mute = Mock()
    pause = Mock()
    anchor = Mock()
    monkeypatch.setattr(video_player, "set_playback_speed", speed)
    monkeypatch.setattr(video_player, "set_muted", mute)
    holder = {}

    @ui.page("/player-source-lifetime")
    def page():
        player = video_player.render_custom_video_player(
            "/first.mp4", 10, "source-lifetime", autoplay=False
        )
        distance_player.install_manual_pause_snap(player, lambda event: pause(event))
        distance_player.install_anchor_editor(player, lambda event: anchor(event))
        holder["player"] = player

    await user.open("/player-source-lifetime")
    player = holder["player"]
    listeners = dict(player._event_listeners)
    # The generated browser code must emit on the owning component, not layout 0.
    assert "emitEvent(" not in "\n".join(scripts)
    assert "comp.$emit('vp_speed_change_source-lifetime'" in "\n".join(scripts)
    assert "component.$emit('distance_anchor_" in "\n".join(scripts)

    monkeypatch.setattr(ui, "run_javascript", AsyncMock(return_value=True))
    with player.client:
        assert await distance_player.switch_source(player, "/second.mp4", 1000)
        assert await distance_player.switch_source(player, "/third.mp4", 2000)
        assert player._event_listeners == listeners
        for name, payload in (
            ("vp_speed_change_source-lifetime", 2),
            ("vp_mute_change_source-lifetime", False),
            (f"distance_manual_pause_{player.id}", {"offset_ms": 2000}),
            (f"distance_anchor_{player.id}", {"anchor": {"x": 0.1}}),
        ):
            listener = next(
                item
                for item in listeners.values()
                if item.type == helpers.event_type_to_camel_case(name)
            )
            player._handle_event({"listener_id": listener.id, "args": payload})

    speed.assert_called_once_with("2.0x")
    mute.assert_called_once_with(False)
    assert pause.call_args.args[0].args == {"offset_ms": 2000}
    assert anchor.call_args.args[0].args == {"anchor": {"x": 0.1}}
