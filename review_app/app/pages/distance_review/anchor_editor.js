(function install() {
    const identities = window.__distanceIndividuals ||= new Map();
    const component = getElement(__PLAYER_ID__);
    const video = component?.$el;
    if (!video) { setTimeout(install, 50); return; }
    if (video._distanceAnchor) return;
    const container = video.parentElement;
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:absolute;inset:0;pointer-events:none;touch-action:none;z-index:2';
    container.appendChild(overlay);
    let individual = null, individuals = [], mode = null, start = null, preview = null;
    let masks = [], showMasks = true;
    const maskPaths = new WeakMap();

    function contentRect() {
        const rect = video.getBoundingClientRect();
        if (!video.videoWidth || !video.videoHeight) return null;
        const scale = Math.min(rect.width / video.videoWidth, rect.height / video.videoHeight);
        const width = video.videoWidth * scale, height = video.videoHeight * scale;
        return {left: rect.left + (rect.width - width) / 2,
            top: rect.top + (rect.height - height) / 2, width, height};
    }
    function normalizedPosition(event) {
        const rect = contentRect();
        if (!rect) return null;
        const x = (event.clientX - rect.left) / rect.width;
        const y = (event.clientY - rect.top) / rect.height;
        return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? {x, y} : null;
    }
    function maskPath(row) {
        if (maskPaths.has(row)) return maskPaths.get(row);
        const height = row.size[0];
        const path = [];
        const rectangle = (x, y, width, height) => {
            if (width && height) path.push(`M${x} ${y}h${width}v${height}h${-width}z`);
        };
        let position = 0;
        row.counts.forEach((length, index) => {
            if (index % 2 && length) {
                let x = Math.floor(position / height), remaining = length;
                const y = position % height, first = Math.min(remaining, height - y);
                rectangle(x, y, 1, first);
                remaining -= first;
                x++;
                const columns = Math.floor(remaining / height);
                rectangle(x, 0, columns, height);
                rectangle(x + columns, 0, 1, remaining % height);
            }
            position += length;
        });
        const result = path.join('');
        maskPaths.set(row, result);
        return result;
    }
    function paintMasks(rect, outer, toleranceMs) {
        // These are original-frame proposals, not tracking/interpolated masks.
        if (!showMasks || mode || !video.paused || video.seeking || video.readyState < 2) return;
        for (const row of masks) {
            if (Math.abs(video.currentTime * 1000 - row.time_s * 1000) >= toleranceMs) continue;
            const path = maskPath(row);
            if (!path) continue;
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', `0 0 ${row.size[1]} ${row.size[0]}`);
            svg.setAttribute('preserveAspectRatio', 'none');
            svg.style.cssText = 'position:absolute;pointer-events:none;opacity:0.35';
            svg.style.left = `${rect.left - outer.left}px`;
            svg.style.top = `${rect.top - outer.top}px`;
            svg.style.width = `${rect.width}px`;
            svg.style.height = `${rect.height}px`;
            const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            shape.setAttribute('d', path);
            shape.setAttribute('fill', row.individual_id === individual?.id ? '#fbbf24' : '#9e9e9e');
            svg.appendChild(shape);
            overlay.appendChild(svg);
        }
    }
    function repaint() {
        const rect = contentRect(), outer = container.getBoundingClientRect();
        overlay.replaceChildren();
        if (!rect) return;
        const displayed = Math.round(video.currentTime * 1000);
        const rate = __FRAME_RATE__;
        const toleranceMs = rate > 0 ? 500 / rate : 10;
        paintMasks(rect, outer, toleranceMs);
        const matching = individuals.filter(row => row.anchor_x != null && row.anchor_y != null &&
            row.anchor_width > 0 && row.anchor_height > 0 && video.paused &&
            Math.abs(displayed - (row.first_seen_playback_ms ?? row.first_seen_offset_ms)) < toleranceMs);
        if (preview && individual) matching.push({...individual, ...{
            anchor_x: preview.x, anchor_y: preview.y,
            anchor_width: preview.width, anchor_height: preview.height,
        }});
        // Pixel-only insets separate shared edges; identity order, not selection, owns each lane.
        const boxes = matching.map(row => ({row,
            left: rect.left - outer.left + row.anchor_x * rect.width,
            top: rect.top - outer.top + row.anchor_y * rect.height,
            width: row.anchor_width * rect.width, height: row.anchor_height * rect.height,
        }));
        const ordered = [...boxes].sort((a, b) => String(a.row.id).localeCompare(String(b.row.id)));
        const sharedEdge = (a, b) => {
            const close = (x, y) => Math.abs(x - y) < 4;
            return (a.top < b.top + b.height && b.top < a.top + a.height &&
                (close(a.left, b.left) || close(a.left + a.width, b.left + b.width))) ||
                (a.left < b.left + b.width && b.left < a.left + a.width &&
                (close(a.top, b.top) || close(a.top + a.height, b.top + b.height)));
        };
        const bounds = {left: Math.max(0, rect.left - outer.left),
            top: Math.max(0, rect.top - outer.top),
            right: Math.min(outer.width, rect.left - outer.left + rect.width),
            bottom: Math.min(outer.height, rect.top - outer.top + rect.height)};
        const outlines = [];
        for (const box of ordered) {
            const limit = Math.min(box.width, box.height) / 3;
            let inset = 0;
            const displayBox = () => ({left: box.left + inset, top: box.top + inset,
                width: box.width - inset * 2, height: box.height - inset * 2});
            while (inset < limit && outlines.some(other => sharedEdge(displayBox(), other))) {
                inset = Math.min(inset + 5, limit);
            }
            // Very small boxes cannot hold nested borders; stagger them toward the viewport center.
            const dx = box.left + box.width / 2 < (bounds.left + bounds.right) / 2 ? 5 : -5;
            const dy = box.top + box.height / 2 < (bounds.top + bounds.bottom) / 2 ? 5 : -5;
            while (outlines.some(other => sharedEdge(displayBox(), other))) {
                box.left += dx;
                box.top += dy;
            }
            box.inset = inset;
            outlines.push(displayBox());
        }
        const labels = [];
        for (const box of boxes) {
            const {row, inset} = box;
            const selected = row.id === individual?.id;
            const color = selected ? '#fbbf24' : '#9e9e9e';
            const marker = document.createElement('div');
            marker.style.cssText = `position:absolute;pointer-events:none;border:3px solid ${color};box-sizing:border-box;border-radius:2px;box-shadow:0 0 0 1px #111,inset 0 0 0 1px #111`;
            marker.style.left = `${box.left + inset}px`;
            marker.style.top = `${box.top + inset}px`;
            marker.style.width = `${box.width - inset * 2}px`;
            marker.style.height = `${box.height - inset * 2}px`;
            overlay.appendChild(marker);
            if (row.individual_number != null) {
                const label = document.createElement('span');
                label.textContent = `#${row.individual_number}`;
                label.style.cssText = `position:absolute;z-index:1;background:${color};color:#111;font:600 13px/16px sans-serif;padding:2px 5px;white-space:nowrap;box-shadow:0 0 0 1px #111`;
                marker.appendChild(label);
                const {width, height} = label.getBoundingClientRect();
                const left = Math.max(bounds.left, Math.min(box.left, bounds.right - width));
                const preferred = Math.max(bounds.top, Math.min(box.top - height - 2, bounds.bottom - height));
                // Try nearby vertical slots, including above bottom-edge labels, without moving saved anchors.
                const slots = [preferred, ...labels.flatMap(other => [other.bottom + 3, other.top - height - 3])];
                const top = slots.find(y => y >= bounds.top && y + height <= bounds.bottom &&
                    labels.every(other => left + width + 3 <= other.left || left >= other.right + 3 ||
                        y + height + 3 <= other.top || y >= other.bottom + 3)) ?? preferred;
                label.style.left = `${left - box.left - inset - 3}px`;
                label.style.top = `${top - box.top - inset - 3}px`;
                labels.push({left, top, right: left + width, bottom: top + height});
            }
        }
    }
    function setMode(value) {
        mode = value === 'box' ? 'box' : null;
        start = preview = null;
        overlay.style.pointerEvents = mode ? 'auto' : 'none';
        overlay.style.cursor = mode ? 'crosshair' : '';
        if (mode) {
            if (!video.paused) video._distanceSuppressedPauseEvents =
                (video._distanceSuppressedPauseEvents || 0) + 1;
            video.pause();
        }
        repaint();
    }
    overlay.addEventListener('pointerdown', event => {
        if (!mode || !individual || event.button !== 0 || video.readyState < 2) return;
        if (!video.paused) video._distanceSuppressedPauseEvents =
            (video._distanceSuppressedPauseEvents || 0) + 1;
        video.pause();
        start = normalizedPosition(event);
        if (!start) return;
        event.preventDefault();
        overlay.setPointerCapture(event.pointerId);
        preview = {...start, width: 0, height: 0};
        repaint();
    });
    overlay.addEventListener('pointermove', event => {
        if (!start || mode !== 'box') return;
        const end = normalizedPosition(event);
        if (!end) return;
        preview = {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
            width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y)};
        repaint();
    });
    overlay.addEventListener('pointerup', event => {
        if (!start) return;
        const end = normalizedPosition(event);
        if (mode !== 'box' || !end) { setMode(__MODE__); return; }
        const anchor = {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
            width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y)};
        if (anchor.width === 0 || anchor.height === 0) { setMode(__MODE__); return; }
        component.$emit('__EVENT__', {
            individual_id: individual.id, offset_ms: Math.round(video.currentTime * 1000), anchor});
        setMode(null);
    });
    overlay.addEventListener('pointercancel', () => setMode(__MODE__));
    const observer = new ResizeObserver(repaint);
    observer.observe(container);
    for (const event of ['timeupdate', 'seeking', 'seeked', 'pause', 'play', 'loadedmetadata', 'loadeddata']) {
        video.addEventListener(event, repaint);
    }
    const transforms = new MutationObserver(repaint);
    transforms.observe(video, {attributes: true, attributeFilter: ['style']});
    video._distanceAnchor = {
        configure(value) {
            const state = value || {};
            individual = state.individual || (Array.isArray(state) ? null : state);
            individuals = state.individuals || (Array.isArray(state) ? state : (individual ? [individual] : []));
            if (individual && !individuals.some(row => row.id === individual.id)) individuals.push(individual);
            masks = state.masks ?? masks;
            showMasks = state.show_masks ?? showMasks;
            identities.set(__PLAYER_ID__, {...state, masks, show_masks: showMasks});
            setMode(__MODE__);
        },
        setMode,
    };
    video._distanceAnchor.configure(identities.has(__PLAYER_ID__) ? identities.get(__PLAYER_ID__) : __INITIAL__);
    const cleanup = new MutationObserver(() => {
        if (video.isConnected) return;
        observer.disconnect(); transforms.disconnect(); cleanup.disconnect();
        setTimeout(() => {
            if (getElement(__PLAYER_ID__)) install();
            else identities.delete(__PLAYER_ID__);
        }, 50);
    });
    cleanup.observe(document.body, {childList: true, subtree: true});
})();
