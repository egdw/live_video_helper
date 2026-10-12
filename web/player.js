'use strict';
const $ = id => document.getElementById(id);
const video = $('courseVideo');
video.controls = false;
video.muted = false;
video.volume = 1;
let sessions = [],
    clockOffset = 0,
    activeKey = '',
    activeIndex = -1,
    activeSession = null,
    activeVideo = null;
let mode = 'paused',
    errorMessage = '',
    lastVideoLog = '',
    lastPositionSetAt = 0,
    lastAutoplayAttempt = 0,
    lastWaitingLog = '';
let enableAudioClicked = false,
    refreshInProgress = false,
    playbackBlocked = false,
    failedKey = '';
const dateTime = (day, clock) => new Date(`${day}T${clock}:00`).getTime();
const nowMs = () => Date.now() + clockOffset;
async function post(path, obj) { try { await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj), cache: 'no-store', keepalive: true }); } catch {} }

function event(kind, detail) { post('/api/event', { kind, detail }); }

function overlay(main, secondary) {
    $('overlay').classList.remove('hidden');
    $('courseVideo').style.display = 'none';
    $('mainMessage').textContent = main;
    $('secondaryMessage').textContent = secondary;
}

function clearVideo() {
    if (video.getAttribute('src')) {
        video.pause();
        video.removeAttribute('src');
        video.load();
    }
    activeVideo = null;
    activeKey = '';
    activeIndex = -1;
}

function noSchedule() {
    if (mode !== 'paused') {
        clearVideo();
        event('player', '直播暂停：当前无课程时段');
    }
    mode = 'paused';
    activeSession = null;
    errorMessage = '';
    playbackBlocked = false;
    overlay('直播暂停', '当前不在课程播放时段');
    $('errorBanner').classList.add('hidden');
}

function selectAt(s, now) {
    let elapsed = (now - dateTime(s.day, s.start_time)) / 1000;
    if (!s.videos.length) return { kind: 'error', message: '本时段没有安排视频，请在管理后台添加课程' };
    if (s.videos.some(v => !Number.isFinite(Number(v.duration)) || Number(v.duration) <= 0))
        return { kind: 'error', message: '本时段有视频时长未识别，请先在管理后台识别后再开播' };
    for (let i = 0; i < s.videos.length; i++) {
        let len = Number(s.videos[i].duration);
        if (elapsed < len - .001) return { kind: 'play', index: i, video: s.videos[i], position: Math.max(0, elapsed) };
        elapsed -= len;
    }
    return { kind: 'finished' };
}

function showError(message, session) {
    if (mode !== 'error' || errorMessage !== message) event('player_error', message);
    mode = 'error';
    errorMessage = message;
    if (activeVideo) clearVideo();
    overlay('播放异常', '请管理员检查课程配置');
    $('errorBanner').textContent = message;
    $('errorBanner').classList.remove('hidden');
}

function blocked(message) {
    playbackBlocked = true;
    $('errorBanner').classList.remove('hidden');
    $('errorBanner').textContent = message;
    $('unlockButton').textContent = '点击解锁播放';
    if (lastWaitingLog !== message) {
        lastWaitingLog = message;
        event('autoplay_blocked', message);
    }
}
async function attemptPlay() {
    if (mode !== 'playing' || !activeVideo || !video.paused) return;
    if (Date.now() - lastAutoplayAttempt < 3800) return;
    lastAutoplayAttempt = Date.now();
    try {
        await video.play();
        playbackBlocked = false;
        $('errorBanner').classList.add('hidden');
    } catch (err) {
        if (err.name === 'AbortError') return;
        blocked(`浏览器阻止有声自动播放：${err.message}。请将鼠标移到画面右上角点击“启用声音”，或用附带的自动播放启动脚本打开浏览器。`);
    }
}

function loadVideo(s, item) {
    const v = item.video;
    const key = `${s.id}:${item.index}:${v.id}`;
    if (activeKey === key) return;
    video.pause();
    activeKey = key;
    activeIndex = item.index;
    activeVideo = v;
    video.src = `/media/${v.id}`;
    video.load();
    mode = 'playing';
    errorMessage = '';
    playbackBlocked = false;
    $('overlay').classList.add('hidden');
    $('courseVideo').style.display = 'block';
    $('errorBanner').classList.add('hidden');
    if (lastVideoLog !== key) {
        lastVideoLog = key;
        event('video_start', `${s.day} ${s.slot} 第${item.index+1}个：${v.title}`);
    }
}

function syncPlayback() {
    const now = nowMs();
    $('clock').textContent = new Date(now).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const s = sessions.find(s => dateTime(s.day, s.start_time) <= now && now < dateTime(s.day, s.end_time));
    if (!s) { noSchedule(); return; }
    activeSession = s;
    const selected = selectAt(s, now);
    if (selected.kind === 'error') { showError(selected.message, s); return; }
    if (selected.kind === 'finished') {
        if (mode !== 'finished') {
            clearVideo();
            event('player', `${s.day} ${s.slot} 全部课程视频已播放完毕`);
        }
        mode = 'finished';
        $('errorBanner').classList.add('hidden');
        overlay('课程已播放完毕', '当前时段视频已结束');
        return;
    }
    const item = selected;
    const nextKey = `${s.id}:${item.index}:${item.video.id}`;
    if (failedKey === nextKey) { showError(`视频播放失败：${item.video.title}。请检查视频文件和编码，或刷新播放页重试。`, s); return; }
    loadVideo(s, item);
    // 恢复播放基于计划绝对时间：意外刷新、晚入会不会从第一段开始。
    if (video.readyState >= 1) {
        const target = Math.max(0, Math.min(Number(item.position), Number(video.duration) || Number(item.video.duration)));
        const drift = Math.abs((video.currentTime || 0) - target);
        const recentLoad = Date.now() - lastPositionSetAt < 2000;
        if (drift > 2.5 && !video.seeking && (!recentLoad || drift > 15)) {
            try {
                video.currentTime = target;
                lastPositionSetAt = Date.now();
            } catch {}
        }
    }
    if (video.paused) attemptPlay();
}
async function refreshPlan() {
    if (refreshInProgress) return;
    refreshInProgress = true;
    try {
        const begin = Date.now();
        const r = await fetch('/api/plan', { cache: 'no-store' });
        if (!r.ok) throw new Error('服务HTTP ' + r.status);
        const j = await r.json();
        const end = Date.now();
        clockOffset = j.server_now_ms - (begin + end) / 2;
        sessions = j.sessions || [];
        if (mode === 'error' && errorMessage === '连接服务失败') errorMessage = '';
        syncPlayback();
    } catch (err) {
        if (!sessions.length) showError('连接服务失败：' + err.message, null);
        else $('errorBanner').textContent = '与本地服务器通信失败，正在尝试重新连接';
    } finally { refreshInProgress = false; }
}
video.addEventListener('loadedmetadata', () => {
    syncPlayback();
    attemptPlay();
});
video.addEventListener('canplay', () => attemptPlay());
video.addEventListener('ended', () => syncPlayback());
video.addEventListener('error', () => {
    if (mode === 'playing' && activeVideo) {
        failedKey = activeKey;
        showError(`播放文件出错：${activeVideo.title}。请检查文件、浏览器编码支持和视频路径。`, activeSession);
    }
});
video.addEventListener('waiting', () => {
    if (mode === 'playing' && !playbackBlocked) {
        $('errorBanner').classList.remove('hidden');
        $('errorBanner').textContent = '视频缓冲中。如果持续不恢复，请检查视频文件。';
    }
});
video.addEventListener('playing', () => { if (mode === 'playing') $('errorBanner').classList.add('hidden'); });
$('unlockButton').addEventListener('click', async() => {
    enableAudioClicked = true;
    try {
        const ctx = new(window.AudioContext || window.webkitAudioContext)();
        await ctx.resume();
        const o = ctx.createOscillator(),
            g = ctx.createGain();
        g.gain.value = 0;
        o.connect(g);
        g.connect(ctx.destination);
        o.start();
        o.stop(ctx.currentTime + .04);
        setTimeout(() => ctx.close(), 300);
    } catch {}
    lastAutoplayAttempt = 0;
    video.muted = false;
    if (activeVideo) {
        try {
            await video.play();
            playbackBlocked = false;
            $('errorBanner').classList.add('hidden');
        } catch (err) { blocked(`无法播放：${err.message}`); }
    }
    $('unlockButton').textContent = '声音已启用 ✓';
    event('player', '用户点击了启用声音');
});
$('fullscreenButton').addEventListener('click', () => {
    const p = $('stage');
    if (document.fullscreenElement) { document.exitFullscreen(); } else p.requestFullscreen().catch(() => {});
});

function heartbeat() {
    const err = $('errorBanner').classList.contains('hidden') ? '' : $('errorBanner').textContent;
    post('/api/heartbeat', {
        mode,
        session: activeSession ? `${activeSession.day} ${activeSession.slot}` : null,
        video: activeVideo ? activeVideo.title : null,
        position: video.currentTime || 0,
        paused: video.paused,
        ready_state: video.readyState,
        error: err,
        blocked: playbackBlocked,
        client_time: new Date().toISOString()
    });
}
window.addEventListener('focus', () => { refreshPlan(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshPlan(); } });
// 浏览器被最小化或电脑休眠会让页面定时器暂停，恢复时立即按实际系统时间定位。
setInterval(syncPlayback, 1000);
setInterval(refreshPlan, 15000);
setInterval(heartbeat, 12000);
refreshPlan().then(heartbeat);
