// Wait for Vue to mount the refreshed buttons and finish layout.
requestAnimationFrame(() => requestAnimationFrame(() => {
    const strip = getHtmlElement(__STRIP_ID__);
    const selected = strip?.querySelector('[aria-pressed="true"]');
    if (!strip || !selected) return;

    const reveal = () => {
        if (!strip.clientWidth) return;
        const left = strip.getBoundingClientRect().left + strip.clientLeft;
        const right = left + strip.clientWidth;
        const button = selected.getBoundingClientRect();
        // Scroll only this strip, with room for the button outline, never the page.
        if (button.left < left + 4) {
            strip.scrollLeft += button.left - left - 4;
        } else if (button.right > right - 4) {
            strip.scrollLeft += button.right - right + 4;
        }
    };
    reveal();
    // Opening/closing sidebars can hide the selected distance without a rerender.
    const observer = new ResizeObserver(() => {
        if (!strip.isConnected) observer.disconnect();
        else reveal();
    });
    observer.observe(strip);
}));
