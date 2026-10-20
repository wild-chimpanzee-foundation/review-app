const video = __VIDEO__;
if (!video) return false;
video._distanceSourceSwitch?.cancel();
let revealing = false;
video._distancePlayback = (video._distancePlayback || 0) + 1;
video.pause();

const snapshot = document.createElement('canvas');
snapshot.width = video.videoWidth || 1;
snapshot.height = video.videoHeight || 1;
Object.assign(snapshot.style, {
    position: 'absolute', inset: '0', width: '100%', height: '100%',
    pointerEvents: 'none', transform: video.style.transform,
    transformOrigin: video.style.transformOrigin, filter: video.style.filter,
});
if (video.readyState >= 2) snapshot.getContext('2d').drawImage(video, 0, 0);
video.parentElement.appendChild(snapshot);
const visibility = video.style.visibility;
video.style.visibility = 'hidden';

const source = __SOURCE__;
const expected = new URL((source.startsWith('/') ? window.path_prefix || '' : '') + source,
    window.location.href).href;
const events = new AbortController();
let settled = false, sought = false, resolve;
const promise = new Promise(done => { resolve = done; });
const finish = success => {
    if (settled) return;
    settled = true;
    clearTimeout(watchdog);
    events.abort();
    video.style.visibility = visibility;
    snapshot.remove();
    resolve(success);
};
const watchdog = setTimeout(() => finish(false), 15000);
const reveal = () => {
    if (!sought || video.seeking || video.readyState < 2 || revealing) return;
    revealing = true;
    // Let the browser paint the decoded target frame before removing its cover.
    requestAnimationFrame(() => requestAnimationFrame(() => finish(true)));
};
video.addEventListener('loadedmetadata', () => {
    if (video.currentSrc !== expected) return;
    video.pause();
    video.currentTime = Math.max(0, Math.min(__OFFSET__, video.duration || 0));
    sought = true;
    reveal();
}, {signal: events.signal});
video.addEventListener('seeked', reveal, {signal: events.signal});
video.addEventListener('loadeddata', reveal, {signal: events.signal});
video.addEventListener('error', () => finish(false), {signal: events.signal});
video._distanceSourceSwitch = {promise, cancel: () => finish(false)};
return true;
