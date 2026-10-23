/* Session-owned WebRTC, packet forwarding only; no external STUN/TURN. */
(() => {
  "use strict";
  function create({video, onStatus = () => {}, onStats = () => {}, onError = () => {}}) {
    let pc = null, socket = null, timer = null, deadline = null, closed = false;
    let previous = null, totalBytes = 0;
    function close() {
      closed = true;
      clearTimeout(deadline); clearInterval(timer);
      if (socket) { socket.onclose = null; socket.close(); socket = null; }
      if (pc) { pc.ontrack = null; pc.close(); pc = null; }
      video.pause(); video.srcObject = null;
    }
    function fail(reason) {
      if (closed) return;
      close(); onError(reason || "WebRTC無法播放；改用MSE。");
    }
    async function start() {
      if (!window.RTCPeerConnection) { fail("瀏覽器未提供WebRTC；改用MSE。"); return; }
      try {
        pc = new RTCPeerConnection({iceServers: []});
        pc.addTransceiver("video", {direction: "recvonly"});
        pc.addTransceiver("audio", {direction: "recvonly"});
        const remoteStream = new MediaStream();
        video.srcObject = remoteStream;
        pc.ontrack = event => {
          if (closed) return;
          remoteStream.addTrack(event.track);
          video.play().catch(() => {});
        };
        await pc.setLocalDescription(await pc.createOffer());
        await new Promise((resolve, reject) => {
          if (pc.iceGatheringState === "complete") { resolve(); return; }
          const limit = setTimeout(() => reject(new Error("ICE")), 4000);
          pc.onicegatheringstatechange = () => {
            if (!closed && pc.iceGatheringState === "complete") { clearTimeout(limit); resolve(); }
          };
        });
        if (closed) return;
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${protocol}//${location.host}/api/v1/preview/webrtc`);
        socket.onopen = () => {
          if (closed) return;
          onStatus("connecting");
          socket.send(JSON.stringify({type: "offer", sdp: pc.localDescription.sdp}));
        };
        socket.onmessage = async event => {
          if (closed) return;
          try {
            const data = JSON.parse(event.data);
            if (data.type !== "answer" || typeof data.sdp !== "string") { fail("WebRTC來源格式不支援；改用MSE。"); return; }
            await pc.setRemoteDescription({type: "answer", sdp: data.sdp});
          } catch (_) { fail("WebRTC協商失敗；改用MSE。"); }
        };
        socket.onerror = () => fail("WebRTC連線失敗；改用MSE。");
        socket.onclose = () => fail("WebRTC觀看授權或連線已結束；改用MSE。");
        deadline = setTimeout(() => fail("WebRTC未取得影格；改用MSE。"), 10000);
        timer = setInterval(async () => {
          if (closed || !pc) return;
          try {
            const report = await pc.getStats();
            if (closed) return;
            report.forEach(item => {
              if (item.type !== "inbound-rtp" || item.kind !== "video") return;
              const elapsed = previous ? (item.timestamp - previous.time) / 1000 : 0;
              const frames = Number(item.framesDecoded || 0);
              const bytes = Number(item.bytesReceived || 0);
              const fps = elapsed > 0 ? Math.max(0, frames - previous.frames) / elapsed : 0;
              const mbps = elapsed > 0 ? Math.max(0, bytes - totalBytes) * 8 / elapsed / 1e6 : 0;
              totalBytes = bytes; previous = {frames, time: item.timestamp};
              if (frames > 0) { clearTimeout(deadline); onStatus("streaming"); }
              onStats({fps, bytes, mbps, transport: "WebRTC", framesDropped: item.framesDropped ?? null});
            });
          } catch (_) { fail("WebRTC播放中斷；改用MSE。"); }
        }, 1000);
      } catch (_) { fail("WebRTC初始化失敗；改用MSE。"); }
    }
    return {start, close};
  }
  window.MeowKeeperRTC = {create};
})();
