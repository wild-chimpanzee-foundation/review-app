const video = __VIDEO__;
if (!video || video.readyState < 2 || !Number.isFinite(video.duration)) return false;
return await new Promise(resolve => {
    if (!video.paused) video._distanceSuppressedPauseEvents =
        (video._distanceSuppressedPauseEvents || 0) + 1;
    video.pause();
    const token = video._distancePlayback = (video._distancePlayback || 0) + 1;
    const startTime = __FROM__, endTime = __TO__;
    const maxDelay = __MAX_DELAY__;
    const frameCallbacks = typeof video.requestVideoFrameCallback === 'function';
    let displayedTime;
    let settled = false, phase = 'starting', animation, frame, boundary;
    const current = () => video.isConnected && video._distancePlayback === token;
    const at = time => !video.seeking && Math.abs(video.currentTime - time) < 0.000005;
    const after = time => !video.seeking && video.currentTime >= time &&
        video.currentTime <= time + maxDelay;
    const finish = (completed = false) => {
        if (settled) return;
        settled = true;
        clearTimeout(watchdog);
        clearTimeout(boundary);
        cancelAnimationFrame(animation);
        if (frame !== undefined) video.cancelVideoFrameCallback?.(frame);
        for (const event of ['timeupdate', 'ratechange', 'ended']) video.removeEventListener(event, check);
        video.removeEventListener('seeked', onSeeked);
        if (current()) {
            if (!video.paused) video._distanceSuppressedPauseEvents =
                (video._distanceSuppressedPauseEvents || 0) + 1;
            video.pause();
        }
        resolve(completed);
    };
    const watchdog = setTimeout(() => finish(), __WATCHDOG__);
    const stopAtBoundary = () => {
        phase = 'stopping';
        if (!video.paused) video._distanceSuppressedPauseEvents =
            (video._distanceSuppressedPauseEvents || 0) + 1;
        video.pause();
        if (at(endTime)) { finish(video.readyState >= 2); return; }
        video.currentTime = endTime;
        video.addEventListener('seeked', function onBoundarySeeked() {
            video.removeEventListener('seeked', onBoundarySeeked);
            finish(current() && video.readyState >= 2 && at(endTime));
        });
    };
    const check = () => {
        if (settled) return;
        if (!current()) { finish(); return; }
        if (phase !== 'playing') return;
        if (video.currentTime >= endTime &&
            (!frameCallbacks || displayedTime >= endTime - 0.0000105)) {
            stopAtBoundary(); return;
        }
        if (video.currentTime > endTime + maxDelay) { finish(); return; }
        if (video.paused || video.seeking) { finish(); return; }
        clearTimeout(boundary);
        boundary = setTimeout(check, Math.max(1,
            (endTime - video.currentTime) * 1000 / video.playbackRate));
    };
    const tick = () => {
        check();
        if (!settled) animation = requestAnimationFrame(tick);
    };
    const frameTick = (now, metadata) => {
        displayedTime = metadata.mediaTime;
        check();
        if (!settled && phase === 'playing') frame = video.requestVideoFrameCallback(frameTick);
    };
    const start = () => {
        if (settled || !current()) { finish(); return; }
        if (!at(startTime) && !after(startTime)) { finish(); return; }
        phase = 'playing';
        video.play().then(() => {
            if (settled || !current()) { finish(); return; }
            check();
            if (!settled) {
                animation = requestAnimationFrame(tick);
                if (video.requestVideoFrameCallback && phase === 'playing') {
                    frame = video.requestVideoFrameCallback(frameTick);
                }
            }
        }).catch(() => finish());
    };
    const onSeeked = () => {
        if (settled || !current()) { finish(); return; }
        if (phase === 'starting') start();
    };
    for (const event of ['timeupdate', 'ratechange', 'ended']) video.addEventListener(event, check);
    video.addEventListener('seeked', onSeeked);
    if (at(startTime) || after(startTime)) start();
    else video.currentTime = startTime;
});
