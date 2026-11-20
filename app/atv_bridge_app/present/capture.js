// Executed in the trusted top-level stage with a user gesture. Chromium's tab
// capture includes sandboxed iframe audio, including Web Audio and remote pages.
async ({duration, still}) => {
  const tab = await navigator.mediaDevices.getDisplayMedia({
    preferCurrentTab: true, selfBrowserSurface: "include",
    video: {frameRate: 12},
    audio: {suppressLocalAudioPlayback: false, echoCancellation: false,
            noiseSuppression: false, autoGainControl: false}
  });
  const audio = tab.getAudioTracks()[0];
  if (!audio) {
    tab.getTracks().forEach(track => track.stop());
    throw Error("Chromium did not provide tab audio");
  }
  // Keep timestamps advancing even during silence.
  const ctx = new AudioContext();
  const mix = ctx.createMediaStreamDestination();
  const silence = ctx.createConstantSource();
  silence.offset.value = 0;
  silence.connect(mix);
  silence.start();
  ctx.createMediaStreamSource(new MediaStream([audio])).connect(mix);
  await ctx.resume();
  const stream = new MediaStream([...mix.stream.getTracks(), ...(still ? [] : tab.getVideoTracks())]);
  const recorder = new MediaRecorder(stream, {
    mimeType: still ? "audio/webm;codecs=opus" : "video/webm;codecs=vp8,opus",
    videoBitsPerSecond: 2000000
  });
  const chunks = [];
  let wake, ended = false, error, timer;
  const notify = () => { wake?.(); wake = null; };
  const stop = () => {
    clearTimeout(timer);
    if (recorder.state !== "inactive") recorder.stop();
  };
  recorder.ondataavailable = ({data}) => {
    if (data.size) chunks.push(data);
    if (chunks.length > 32) { error = "Capture writer stalled"; stop(); }
    notify();
  };
  recorder.onerror = () => { error = "Tab recording failed"; stop(); notify(); };
  recorder.onstop = () => {
    ended = true;
    tab.getTracks().forEach(track => track.stop());
    stream.getTracks().forEach(track => track.stop());
    ctx.close();
    notify();
  };
  window.stageRecorder = {
    stop,
    async next() {
      while (!chunks.length && !ended && !error) await new Promise(resolve => { wake = resolve; });
      if (error) throw Error(error);
      if (!chunks.length) return null;
      const blob = chunks.shift();
      return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.slice(reader.result.lastIndexOf(",") + 1));
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }
  };
  recorder.start(500);
  timer = setTimeout(stop, duration * 1000);
}
