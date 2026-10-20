window.__distanceSampleNavigationEvent = '__DISTANCE_SAMPLE_NAV_EVENT__';
if (window.__distanceKeys) window.__distanceKeys.abort();
window.__distanceKeys = new AbortController();
window.addEventListener('keydown', event => {
    const typing = event.target.closest('input, textarea, select, [contenteditable=true], [role=combobox]');
    const applyToEnd = event.ctrlKey && event.key === 'Enter' && !event.shiftKey;
    if ((event.ctrlKey && !applyToEnd) || event.metaKey || event.altKey || event.repeat || event.isComposing || typing) return;

    // These panels handle keys locally, including confirmations over the clock dialog.
    if (event.target.closest('[data-distance-dialog-keyboard="true"]')) return;

    const clockDialog = document.querySelector('.q-dialog[aria-modal=true][data-distance-clock-dialog="true"]');
    if (clockDialog) {
        if (event.shiftKey || event.ctrlKey) return;
        const step = {ArrowLeft: -1, ArrowRight: 1}[event.key];
        const action = step !== undefined
            ? clockDialog.querySelector(`[data-distance-clock-step="${step}"]`)
            : event.key === 'Enter'
                ? clockDialog.querySelector('[data-distance-clock-action="confirm"]')
                : null;
        if (action) {
            event.preventDefault();
            event.stopPropagation();
            if (!action.disabled) action.click();
        }
        return;
    }
    const dialog = document.querySelector('.q-dialog[aria-modal=true]');
    if (dialog) {
        if (dialog.matches?.('[data-distance-behavior-dialog="true"]')
            && !event.shiftKey && !event.ctrlKey) {
            const step = {ArrowUp: -1, ArrowDown: 1}[event.key];
            if (step !== undefined) {
                const options = Array.from(dialog.querySelectorAll('[data-distance-behavior-option]'))
                    .filter(option => option.getAttribute('aria-disabled') !== 'true');
                if (options.length) {
                    const current = options.findIndex(option => option.contains(document.activeElement));
                    const selected = options.findIndex(option => option.getAttribute('aria-checked') === 'true');
                    const next = current < 0 ? Math.max(0, selected)
                        : (current + step + options.length) % options.length;
                    event.preventDefault();
                    event.stopPropagation();
                    options[next].focus({preventScroll: true});
                    options[next].scrollIntoView({block: 'nearest'});
                }
            } else if (event.key === 'Enter') {
                const confirm = dialog.querySelector('[data-distance-behavior-action="confirm"]');
                if (confirm) {
                    event.preventDefault();
                    event.stopPropagation();
                    if (!confirm.disabled) confirm.click();
                }
            }
        }
        return;
    }
    if (event.key === 'j' || event.key === 'J' || event.key === 'k' || event.key === 'K') {
        const cards = Array.from(document.querySelectorAll('[data-distance-individual]'));
        if (cards.length === 0) return;
        const selected = cards.findIndex(card => card.dataset.distanceActive === 'true');
        const isNext = event.key === 'j' || event.key === 'J';
        const targetIndex = isNext
            ? (selected + 1) % cards.length
            : ((selected - 1 + cards.length) % cards.length);
        const button = cards[targetIndex].querySelector('[data-distance-action="select-individual"]');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        if (!button.disabled) button.click();
        return;
    }
    const arrow = event.key.startsWith('Arrow');
    if (arrow && event.shiftKey) return;
    const sampleDirection = {ArrowLeft: 'previous', ArrowRight: 'next'}[event.key];
    if (sampleDirection && typeof emitEvent === 'function' && window.__distanceSampleNavigationEvent) {
        event.preventDefault();
        event.stopPropagation();
        emitEvent(window.__distanceSampleNavigationEvent, {direction: sampleDirection});
        return;
    }
    const action = applyToEnd ? 'apply-to-end' : {
        ArrowDown: 'distance-down', ArrowUp: 'distance-up',
        Enter: 'confirm', a: 'add', A: 'add', n: 'next', N: 'next', p: 'previous', P: 'previous',
        Tab: event.shiftKey ? 'previous-reference' : 'next-reference', r: 'replay', R: 'replay',
    }[event.key];
    if (!action) return;
    const clockButton = action === 'confirm'
        ? document.querySelector('[data-distance-action="verify-clock"]')
            || document.querySelector('[data-distance-action="rail-clock"]')
        : null;
    const button = clockButton || document.querySelector(`[data-distance-action="${action}"]`);
    // Reserve R even when replay is unavailable; do not reset the player filters.
    if (!button && action !== 'replay') return;
    event.preventDefault();
    event.stopPropagation();
    if (button && !button.disabled) button.click();
}, {signal: window.__distanceKeys.signal, capture: true});
