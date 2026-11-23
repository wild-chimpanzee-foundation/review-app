"""Compare directory levels and highlight changes within folder names."""

import re
from difflib import SequenceMatcher
from html import escape
from itertools import zip_longest
from pathlib import PurePosixPath

from nicegui import ui

from review_app.app.translations import t
from review_app.backend.path_matching import normalize_path_str

_PATH_TOKENS = re.compile(r"[^\W\d_]+|\d+|[\W_]+")


def _highlight(text, changed):
    classes = "text-warning font-semibold underline" if changed else "text-positive"
    return f'<span class="{classes}">{escape(text)}</span>'


def _compare_names(source, target):
    if source is None or target is None:
        return tuple("—" if name is None else _highlight(name, True) for name in (source, target))
    if source == target:
        matching = _highlight(source, False)
        return matching, matching

    left, right = _PATH_TOKENS.findall(source), _PATH_TOKENS.findall(target)
    left_html, right_html = [], []
    for tag, i, j, k, m in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if i != j:
            left_html.append(_highlight("".join(left[i:j]), tag != "equal"))
        if k != m:
            right_html.append(_highlight("".join(right[k:m]), tag != "equal"))
    return "".join(left_html), "".join(right_html)


def render_path_comparison(source_folder, target_folder):
    source = PurePosixPath(normalize_path_str(source_folder)).parts or (".",)
    target = PurePosixPath(normalize_path_str(target_folder)).parts or (".",)

    with ui.card().classes("w-full gap-2").mark("deployment-review-path-comparison"):
        ui.label(t("deployment_review_path_comparison")).classes("text-subtitle1")
        with ui.element("div").classes("w-full grid grid-cols-2 gap-x-4 gap-y-1 items-start"):
            ui.label(t("deployment_review_folder_footage")).classes("text-caption")
            ui.label(t("deployment_review_target_footage")).classes("text-caption")
            for _, i, j, k, m in SequenceMatcher(
                None, source, target, autojunk=False
            ).get_opcodes():
                for left, right in zip_longest(source[i:j], target[k:m]):
                    for content in _compare_names(left, right):
                        ui.html(content).classes(
                            "font-mono text-caption whitespace-pre-wrap break-all"
                        )
