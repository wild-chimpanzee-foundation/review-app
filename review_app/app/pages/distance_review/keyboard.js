window.__distanceSampleNavigationEvent = '__DISTANCE_SAMPLE_NAV_EVENT__';
if (window.__distanceKeys) window.__distanceKeys.abort();
window.__distanceKeys = new AbortController();
window.addEventListener('keydown', event => {
    const typing = event.target.closest('input, textarea, select, [contenteditable=true], [role=combobox]');
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || event.isComposing || typing) return;

    const clockDialog = document.querySelector('.q-dialog[aria-modal=true][data-distance-clock-dialog="true"]');
    if (clockDialog) {
        if (event.shiftKey) return;
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
    if (document.querySelector('.q-dialog[aria-modal=true]')) return;
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
    if (event.key === 'Tab' && event.shiftKey) return;
    const action = {
        ArrowDown: 'distance-down', ArrowUp: 'distance-up',
        Enter: 'confirm', a: 'add', A: 'add', n: 'next', N: 'next', p: 'previous', P: 'previous',
        Tab: 'next-reference',
    }[event.key];
    if (!action) return;
    const button = document.querySelector(`[data-distance-action="${action}"]`);
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    if (!button.disabled) button.click();
}, {signal: window.__distanceKeys.signal, capture: true});
