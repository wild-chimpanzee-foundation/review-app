from nicegui import ui

from review_app.app.state import is_tour_completed, set_tour_completed

_TOUR_CSS = """
<style>
.tour-highlight {
    outline: 5px solid #f59e0b !important;
    outline-offset: 5px !important;
    border-radius: 6px !important;
    animation: tour-pulse 1.8s ease-in-out infinite !important;
}
@keyframes tour-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.55); }
    50% { box-shadow: 0 0 0 10px rgba(245, 158, 11, 0); }
}
</style>
"""

# CSS selector for each step (None = no highlight, dialog centers itself)
_STEP_TARGETS = [
    None,  # 0: Welcome
    ".tour-target-queue",  # 1: Review Queue
    ".tour-target-filters",  # 2: Filters
    ".tour-target-ai-predictions",  # 3: AI Predictions
    ".tour-target-action-buttons",  # 4: Blank vs Non-blank
    ".tour-target-review-later",  # 5: Review Later
    None,  # 6: Done
]

# Highlights the target element and repositions the tour dialog near it.
# Uses a 60ms timeout so the dialog has time to render before measuring its size.
_STEP_JS = """
(function(sel) {
    document.querySelectorAll('.tour-highlight').forEach(function(el) {
        el.classList.remove('tour-highlight');
    });

    var target = sel ? document.querySelector(sel) : null;
    if (target) {
        target.classList.add('tour-highlight');
    }

    setTimeout(function() {
        var inner = document.querySelector('.tour-dialog .q-dialog__inner');
        if (!inner) return;

        inner.style.position = 'fixed';
        inner.style.margin = '0';
        inner.style.transition = 'top 0.25s ease, left 0.25s ease';

        var vw = window.innerWidth;
        var vh = window.innerHeight;
        var dw = inner.offsetWidth || 520;
        var dh = inner.offsetHeight || 240;
        var gap = 20;
        var pad = 16;

        if (!target) {
            inner.style.top  = Math.max(pad, (vh - dh) / 2) + 'px';
            inner.style.left = Math.max(pad, (vw - dw) / 2) + 'px';
            inner.style.bottom = '';
            inner.style.right  = '';
            return;
        }

        // Scroll target into view if off-screen, then position
        var tr = target.getBoundingClientRect();
        if (tr.top < 0 || tr.bottom > vh) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Re-measure after scroll settles
            setTimeout(function() { positionNear(target, dw, dh, vw, vh, gap, pad, inner); }, 320);
        } else {
            positionNear(target, dw, dh, vw, vh, gap, pad, inner);
        }
    }, 60);

    function positionNear(target, dw, dh, vw, vh, gap, pad, inner) {
        var tr = target.getBoundingClientRect();

        // Horizontal: center dialog on target, clamped to viewport
        var left = tr.left + tr.width / 2 - dw / 2;
        left = Math.max(pad, Math.min(left, vw - dw - pad));

        // Vertical: prefer below, then above, then bottom of viewport
        var top = tr.bottom + gap;
        if (top + dh > vh - pad) {
            top = tr.top - dh - gap;
        }
        if (top < pad) {
            top = vh - dh - pad;
        }

        inner.style.top    = top  + 'px';
        inner.style.left   = left + 'px';
        inner.style.bottom = '';
        inner.style.right  = '';
    }
})("%s");
"""


def _step_js(step: int) -> str:
    target = _STEP_TARGETS[step] if step < len(_STEP_TARGETS) else None
    return _STEP_JS % (target or "")


def _clear_highlight() -> None:
    ui.run_javascript(_STEP_JS % "")


def show_info_dialog(title: str, body: str) -> None:
    with (
        ui.dialog() as d,
        ui.card().classes("q-pa-lg relative").style("min-width: 380px; max-width: 540px"),
    ):
        ui.button(icon="close", on_click=d.close).props("flat round").classes(
            "absolute-top-right q-ma-sm"
        )
        ui.label(title).classes("text-subtitle1 text-bold q-mb-sm q-mr-lg")
        ui.label(body).classes("text-body2").style("white-space: pre-wrap; line-height: 1.6")
    d.open()


def show_tour(t) -> None:
    ui.add_head_html(_TOUR_CSS, shared=True)

    steps = [
        (t("tour_step_1_title"), t("tour_step_1_body")),
        (t("tour_step_2_title"), t("tour_step_2_body")),
        (t("tour_step_filters_title"), t("tour_step_filters_body")),
        (t("tour_step_3_title"), t("tour_step_3_body")),
        (t("tour_step_4_title"), t("tour_step_4_body")),
        (t("tour_step_5_title"), t("tour_step_5_body")),
        (t("tour_step_6_title"), t("tour_step_6_body")),
    ]

    state = {"step": 0}
    els: dict = {}

    def update():
        n = state["step"]
        total = len(steps)
        els["progress"].text = f"{n + 1} / {total}"
        els["title"].text = steps[n][0]
        els["body"].text = steps[n][1]
        els["prev"].set_visibility(n > 0)
        els["next"].text = t("tour_finish") if n == total - 1 else t("tour_next")
        ui.run_javascript(_step_js(n))

    def go_prev():
        state["step"] -= 1
        update()

    def go_next():
        if state["step"] < len(steps) - 1:
            state["step"] += 1
            update()
        else:
            _clear_highlight()
            set_tour_completed(True)
            els["dialog"].close()

    def do_skip():
        _clear_highlight()
        set_tour_completed(True)
        els["dialog"].close()

    with (
        ui.dialog().props("seamless").classes("tour-dialog") as dialog,
        ui.card()
        .classes("q-pa-lg shadow-10")
        .style("min-width: 480px; max-width: 600px; border-top: 3px solid #f59e0b"),
    ):
        els["dialog"] = dialog
        with ui.row().classes("w-full items-center q-mb-xs"):
            els["progress"] = ui.label().classes("text-caption text-grey-5")
            ui.space()
            ui.icon("school", size="sm").classes("text-amber-6")
        els["title"] = ui.label().classes("text-h6 q-mb-sm")
        els["body"] = (
            ui.label().classes("text-body2").style("white-space: pre-wrap; line-height: 1.6")
        )

        with ui.row().classes("w-full q-mt-lg items-center"):
            ui.button(t("tour_skip"), on_click=do_skip).props("flat color=grey dense")
            ui.space()
            els["prev"] = ui.button(t("tour_prev"), icon="chevron_left", on_click=go_prev).props(
                "flat"
            )
            els["next"] = ui.button(t("tour_next"), on_click=go_next).props("color=primary")

    update()
    dialog.open()


def show_tour_if_needed(t) -> None:
    if not is_tour_completed():
        show_tour(t)
