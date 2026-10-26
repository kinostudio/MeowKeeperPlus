/* MSE consumer for the authenticated same-origin Dashboard proxy. */
(() => {
  "use strict";
  const candidates = [
    "avc1.640029", "avc1.64002A", "avc1.640033", "avc1.4D401F",
    "avc1.42E01E", "hvc1.1.6.L153.B0",
    "mp4a.40.2", "mp4a.40.5", "opus", "flac"
  ];
  const mimePattern = /^video\/mp4;\s*codecs="([A-Za-z0-9., ]{1,256})"$/;

  function supportedCodecs() {
    if (!window.MediaSource || typeof MediaSource.isTypeSupported !== "function") return [];
    return candidates.filter(codec => {
      try { return MediaSource.isTypeSupported(`video/mp4; codecs="${codec}"`); }
      catch (_) { return false; }
    });
  }

  function create({ video, compatibility = false, onStats = () => {}, onStatus = () => {}, onAudio = () => {}, onError = () => {} }) {
    let socket = null, mediaSource = null, sourceBuffer = null, objectUrl = null;
    let queue = [], queuedBytes = 0, totalBytes = 0, windowBytes = 0, lastStatsAt = null, frames = 0, lastPresentedFrames = null;
    let frameCallback = 0, statsTimer = 0, timeout = 0, closed = false, firstFrame = false, opening = false;
    const codecs = supportedCodecs();
    let audioCodec = null;

    function fail(message) {
      if (closed) return;
      onError(message || "此瀏覽器或攝影機不支援連續播放。");
      close();
    }

    function cleanupMedia() {
      try { video.pause(); } catch (_) { /* best effort */ }
      video.removeAttribute("src");
      try { video.load(); } catch (_) { /* best effort */ }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      sourceBuffer = null;
      mediaSource = null;
      queue = [];
      queuedBytes = 0;
    }

    function close() {
      if (closed) return;
      closed = true;
      if (timeout) clearTimeout(timeout);
      if (statsTimer) clearInterval(statsTimer);
      if (frameCallback && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(frameCallback);
      frameCallback = 0;
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "preview mode changed");
      socket = null;
      cleanupMedia();
      onStatus("closed");
    }

    function frameLoop(now, frameMetadata) {
      if (closed) return;
      const presented = frameMetadata?.presentedFrames;
      if (lastStatsAt === null) lastStatsAt = now;
      else frames += Number.isFinite(presented) && lastPresentedFrames !== null
        ? Math.max(0, presented - lastPresentedFrames) : 1;
      if (Number.isFinite(presented)) lastPresentedFrames = presented;
      if (now - lastStatsAt >= 1000) {
        const elapsed = (now - lastStatsAt) / 1000;
        onStats({ fps: frames / elapsed, bytes: totalBytes, mbps: windowBytes * 8 / elapsed / 1000000 });
        frames = 0;
        windowBytes = 0;
        lastStatsAt = now;
      }
      if (video.requestVideoFrameCallback) frameCallback = video.requestVideoFrameCallback(frameLoop);
    }

    function flush() {
      if (closed || !sourceBuffer || sourceBuffer.updating || !queue.length) return;
      const data = queue.shift();
      queuedBytes -= data.byteLength;
      try {
        sourceBuffer.appendBuffer(data);
      } catch (_) {
        fail("瀏覽器解碼連續影格失敗；已改回節能預覽。");
      }
    }

    function onSourceOpen() {
      if (closed || opening || !mediaSource || !mediaSource._meowMime) return;
      opening = true;
      try {
        sourceBuffer = mediaSource.addSourceBuffer(mediaSource._meowMime);
        sourceBuffer.mode = "segments";
        sourceBuffer.addEventListener("updateend", () => {
          if (closed || !sourceBuffer) return;
          try {
            const ranges = sourceBuffer.buffered;
            if (ranges.length) {
              const edge = ranges.end(ranges.length - 1);
              if (video.currentTime === 0 || edge - video.currentTime > 2.0) video.currentTime = Math.max(0, edge - 0.35);
              const keepFrom = video.currentTime - 20;
              // remove() emits updateend too. Only prune data that still exists,
              // otherwise repeated no-op removals can starve queued appends.
              if (video.currentTime > 25 && ranges.start(0) < keepFrom - 1 && !sourceBuffer.updating) {
                sourceBuffer.remove(ranges.start(0), keepFrom);
              }
            }
          } catch (_) { /* a short-lived MSE range race is harmless */ }
          flush();
          if (video.paused) video.play().catch(() => { /* wait for the next decodable segment */ });
        });
        flush();
      } catch (_) {
        fail("瀏覽器無法建立連續播放緩衝區；已改回節能預覽。");
      }
    }

    function start() {
      if (!codecs.some(codec => /^(avc1|hvc1)\./.test(codec))) {
        fail("此瀏覽器沒有可用的 MSE 影片解碼器；已改回節能預覽。");
        return;
      }
      onStatus("connecting");
      try {
        mediaSource = new MediaSource();
        objectUrl = URL.createObjectURL(mediaSource);
        video.src = objectUrl;
        mediaSource.addEventListener("sourceopen", onSourceOpen, { once: true });
        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${scheme}//${location.host}/api/v1/preview/mse`);
      } catch (_) {
        fail("瀏覽器無法啟動連續播放；已改回節能預覽。");
        return;
      }
      socket.binaryType = "arraybuffer";
      socket.addEventListener("open", () => {
        if (!closed) socket.send(JSON.stringify({ type: "mse", value: codecs.join(","), ...(compatibility ? {compatibility:true} : {}) }));
      }, { once: true });
      socket.addEventListener("message", event => {
        if (closed) return;
        if (typeof event.data === "string") {
          let reply;
          try { reply = JSON.parse(event.data); } catch (_) { fail("連續播放協商回應格式錯誤。"); return; }
          if (reply.type === "error") { fail(String(reply.value || "連續播放不可用；已改回節能預覽。")); return; }
          const match = reply.type === "mse" && typeof reply.value === "string" ? mimePattern.exec(reply.value) : null;
          if (!match || !match[1].split(",").every(codec => /^[A-Za-z0-9.]+$/.test(codec))) {
            fail("攝影機回傳的播放格式不受支援；已改回節能預覽。"); return;
          }
          if (!MediaSource.isTypeSupported(reply.value)) {
            fail("此裝置無法解碼攝影機格式；已改回節能預覽。"); return;
          }
          mediaSource._meowMime = reply.value;
          audioCodec = match[1].split(",").map(codec => codec.trim()).find(codec => /^(mp4a\.|opus$|flac$)/.test(codec)) || null;
          onAudio({ available: Boolean(audioCodec), codec: audioCodec, state: "negotiated" });
          onStatus("negotiated", reply.transcode || null);
          if (mediaSource.readyState === "open") onSourceOpen();
          return;
        }
        if (!(event.data instanceof ArrayBuffer) || !event.data.byteLength) return;
        totalBytes += event.data.byteLength;
        windowBytes += event.data.byteLength;
        if (queuedBytes + event.data.byteLength > 8 * 1024 * 1024) {
          fail("瀏覽器播放緩衝累積過多；已改回節能預覽。"); return;
        }
        queue.push(event.data);
        queuedBytes += event.data.byteLength;
        flush();
      });
      socket.addEventListener("close", event => {
        if (!closed) fail(event.reason || "連續播放連線中止；已改回節能預覽。");
      });
      socket.addEventListener("error", () => {
        if (!closed) fail("連續播放連線失敗；已改回節能預覽。");
      });
      if (video.muted !== false) video.muted = true;
      video.playsInline = true;
      video.autoplay = true;
      video.addEventListener("playing", () => onStatus("streaming"), { once: true });
      video.addEventListener("error", () => fail("瀏覽器無法解碼這台攝影機的連續影片；已改回節能預覽。"), { once: true });
      if (video.requestVideoFrameCallback) {
        const firstFrameCallback = (now, frameMetadata) => {
          if (closed) return;
          if (!firstFrame) {
            firstFrame = true;
            if (timeout) clearTimeout(timeout);
            timeout = 0;
            onStatus("streaming");
            onStats({ fps: 0, bytes: totalBytes, mbps: 0 });
          }
          frameLoop(now, frameMetadata);
        };
        frameCallback = video.requestVideoFrameCallback(firstFrameCallback);
      } else {
        video.addEventListener("timeupdate", () => {
          if (video.currentTime > 0 && !firstFrame) {
            firstFrame = true;
            if (timeout) clearTimeout(timeout);
            timeout = 0;
            onStatus("streaming");
          }
        }, { once: true });
      }
      if (!video.requestVideoFrameCallback) {
        let previousFrames = 0, previousAt = performance.now();
        statsTimer = setInterval(() => {
          if (closed || !firstFrame) return;
          const now = performance.now(), elapsed = (now - previousAt) / 1000;
          const quality = video.getVideoPlaybackQuality?.();
          const currentFrames = Number(quality?.totalVideoFrames);
          const fps = Number.isFinite(currentFrames) && currentFrames >= previousFrames
            ? (currentFrames - previousFrames) / elapsed : null;
          const mbps = windowBytes * 8 / elapsed / 1000000;
          onStats({ fps, bytes: totalBytes, mbps });
          windowBytes = 0;
          if (Number.isFinite(currentFrames)) previousFrames = currentFrames;
          previousAt = now;
        }, 1000);
      }
      timeout = setTimeout(() => {
        if (!firstFrame) fail("限時內沒有解碼到連續影格；改試相容播放或節能預覽。");
      }, compatibility ? 25000 : 10000);
      video.play().catch(() => {
        if (closed) return;
        // Muted autoplay is normally allowed; retry muted if unmuted playback is blocked.
        if (!video.muted) {
          video.muted = true;
          onAudio({ available: Boolean(audioCodec), codec: audioCodec, blocked: true });
          video.play().catch(() => {});
        }
      });
    }

    return { start, close, codecs };
  }

  window.MeowKeeperMSE = { create, supportedCodecs };
})();
