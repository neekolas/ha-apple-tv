"""Exercise real Debian Chromium and FFmpeg in a network-isolated app container."""

import asyncio
import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from aiohttp import web

from atv_bridge_app.present.browsers import browser_status
from atv_bridge_app.present.render import StageRenderer
from atv_bridge_app.present.sender import STAGE_URL
from atv_bridge_app.stage import Stage


def check_clip(mp4, duration, audible, motion):
    report = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", mp4]
        )
    )
    video, audio = report["streams"]
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert abs(float(report["format"]["duration"]) - duration) < 0.6, report["format"]
    raw = mp4.read_bytes()
    assert 0 < raw.index(b"moov") < raw.index(b"mdat")
    pcm = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", mp4, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    )
    peak = max(abs(sample[0]) for sample in struct.iter_unpack("<h", pcm))
    assert (peak > 1000) == audible, (audible, peak)
    if audible:
        # Both pages produce 440 Hz. Distinguish genuine page audio from
        # codec noise, clicks and an unrelated capture source.
        samples = [sample for (sample,) in struct.iter_unpack("<h", pcm)]
        middle = len(samples) // 2
        segment = samples[middle : middle + 2000]

        def power(frequency):
            phase = math.tau * frequency / 8000
            x = sum(v * math.cos(i * phase) for i, v in enumerate(segment))
            y = sum(v * math.sin(i * phase) for i, v in enumerate(segment))
            return x * x + y * y

        assert power(440) > 20 * max(power(400), power(480))
    pixels = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            mp4,
            "-an",
            "-vf",
            "crop=100:80:16:16,scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    channels = list(zip(*struct.iter_unpack("BBB", pixels)))
    variation = max(max(ch) - min(ch) for ch in channels)
    assert (variation > 25) == motion, variation
    print(
        f"H.264/AAC faststart, {duration:.1f} s, audio peak {peak}, color change {variation}",
        flush=True,
    )


async def main(root):
    stage = Stage(root)
    with wave.open(str(stage.media / "tone.wav"), "wb") as f:
        f.setparams((1, 2, 8000, 0, "NONE", ""))
        f.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(n * math.tau * 440 / 8000))) for n in range(32000)
            )
        )
    connections = []

    def trap(reader, writer):
        connections.append(writer.get_extra_info("peername"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        writer.close()

    sentinel = await asyncio.start_server(trap, "127.0.0.1", 8765)
    runner = web.AppRunner(stage.app())
    renderer = StageRenderer(stage)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 8766).start()
        stage.stage_ports.add(8766)
        for html, duration, audible, motion in [
            ("<body style='background:blue'>static", 60, False, False),
            (
                "<body style='background:green'><audio autoplay src='/m/tone.wav'></audio>",
                4,
                True,
                False,
            ),
            (
                """<canvas></canvas><script>
                const c=document.querySelector('canvas').getContext('2d');let i=0;
                setInterval(()=>{c.fillStyle=++i%2?'blue':'red';c.fillRect(0,0,300,150)},150);
                const a=new AudioContext(), osc=a.createOscillator();
                osc.connect(a.destination);osc.start();a.resume();
                </script>""",
                2,
                True,
                True,
            ),
        ]:
            page = await stage.store.put(html=html)
            await stage.show(page, f"/c/{page}/index.html")
            await renderer.open(STAGE_URL)
            assert browser_status()["processes"] > 0
            if motion:
                renderer.default_duration = duration
                owner = await renderer.page.locator("iframe.visible").element_handle()
                frame = await owner.content_frame()
                reached = await frame.evaluate("""async () => {
                    try {
                        await fetch('http://127.0.0.1:8765/', {mode: 'no-cors'});
                        return true;
                    } catch { return false; }
                }""")
                assert not reached and not connections, connections
                assert renderer.proxy.denial_for("http://127.0.0.1:8765/")
            with tempfile.TemporaryDirectory(dir=root) as work:
                path, captured = await renderer.render(Path(work))
                assert abs(captured - duration) < 0.1
                check_clip(path, duration, audible, motion)
    finally:
        await renderer.close()
        await runner.cleanup()
        sentinel.close()
        await sentinel.wait_closed()
    assert browser_status() == {"processes": 0, "rss_mib": 0}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        asyncio.run(main(Path(temporary)))
