(event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
        || event.repeat || event.isComposing) return;
    // Let fields, dropdowns, and player sliders handle their own keys.
    if (event.target.closest('input, textarea, select, [contenteditable=true], '
        + '[role=combobox], [role=listbox], [role=option], [role=slider], [role=spinbutton]')) return;
    if (event.target.closest('[id^="vp-wrap-"]')) return;

    const panel = event.currentTarget;
    const enabled = control => !control.disabled
        && control.getAttribute('aria-disabled') !== 'true'
        && control.getClientRects().length > 0;
    const step = {ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1}[event.key];
    if (step !== undefined) {
        const controls = Array.from(panel.querySelectorAll('button, input, textarea, select, [tabindex]'))
            .filter(control => control.tabIndex >= 0 && enabled(control));
        if (!controls.length) return;
        const current = controls.findIndex(control => control.contains(document.activeElement));
        const next = current < 0 ? (step > 0 ? 0 : controls.length - 1)
            : (current + step + controls.length) % controls.length;
        event.preventDefault();
        event.stopPropagation();
        controls[next].focus({preventScroll: true});
        controls[next].scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Enter') {
        const focused = event.target.closest('button, [role=button]');
        const action = focused && panel.contains(focused) ? focused
            : panel.querySelector('[data-distance-dialog-action="confirm"]');
        if (!action) return;
        event.preventDefault();
        event.stopPropagation();
        if (enabled(action)) action.click();
    }
    // Tab, Shift+Tab, Space, and Escape retain native dialog/control behavior.
}
