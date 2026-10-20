() => {
    const focused = getHtmlElement(__FOCUS_BUTTON_ID__).getAttribute('aria-pressed') === 'true';
    // Request fullscreen directly in the click handler, before the server round trip
    // consumes the browser's transient user activation. Focus still works if denied.
    try {
        if (focused) {
            if (document.fullscreenElement === document.documentElement) {
                document.exitFullscreen?.().catch(() => {});
            }
        } else if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen?.().catch(() => {});
        }
    } catch {
        // Unsupported or blocked fullscreen must not prevent the layout change.
    }
    emit();
}
