"""Run tvpipe against a loopback receiver. One workload per fresh container."""
import argparse
import array
import base64
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request

import websocket
from PIL import Image

HERE = Path(__file__).resolve().parent
CLK = os.sysconf("SC_CLK_TCK")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def stage(page):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/stage.html?page={page}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Receiver:
    """Read v1 browser-player records, acknowledge bytes and measure PCM."""
    def __init__(self, url):
        self.video = self.audio = self.keys = self.peak = self.crossings = self.samples = 0
        self.last_sample = None
        self.config = {}
        self.error = None
        self.ws = websocket.create_connection(url.replace("http:", "ws:").replace("/?", "/ws?"),
                                              suppress_origin=True, timeout=20)
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self):
        total = acked = 0
        try:
            while True:
                msg = self.ws.recv()
                if not msg:
                    return
                if isinstance(msg, str):
                    event = json.loads(msg)
                    if event["type"] == "config":
                        self.config = event
                    if event["type"] == "end":
                        return
                    continue
                kind, _, length = struct.unpack_from("<BQI", msg)
                assert len(msg) == length + 13
                if kind == 3:
                    self.video += 1
                    self.keys += msg[13] & 1
                elif kind == 2:
                    self.audio += 1
                    # Check a contiguous half-second after startup.
                    if 100 <= self.audio < 170:
                        channels = self.config["audio"]["channels"]
                        pcm = array.array("h", msg[13:])[::channels]
                        self.peak = max(self.peak, max(pcm), -min(pcm))
                        if self.last_sample is not None:
                            self.crossings += self.last_sample <= 0 < pcm[0]
                            self.samples += 1
                        self.last_sample = pcm[-1]
                        self.crossings += sum(a <= 0 < b for a, b in zip(pcm, pcm[1:]))
                        self.samples += len(pcm) - 1
                total += len(msg)
                if total - acked >= 32768:
                    self.ws.send(json.dumps({"type": "ack", "bytes": total}))
                    acked = total
        except Exception as exc:
            self.error = str(exc)

    def close(self):
        self.ws.close()
        self.thread.join(5)


def record(kind, pts, data):
    return struct.pack("<BQI", kind, pts, len(data)) + data


class Sender:
    def __init__(self, args):
        self.proc = subprocess.Popen(["tvpipe", "--machine", "--debug", *args],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.proc.stdin.write(b"SLRW")
        self.proc.stdin.flush()
        self.receiver = None
        self.connected = threading.Event()
        self.capture = self.started = self.stats = None
        self.share_stats = []
        self.logs = []
        self.error = None
        self.thread = threading.Thread(target=self.read)
        self.thread.start()

    def read(self):
        try:
            for line in self.proc.stdout:
                event = json.loads(line)
                kind = event["type"]
                if kind == "player":
                    self.receiver = Receiver(event["url"])
                elif kind == "connected":
                    self.connected.set()
                elif kind == "capture":
                    self.capture = {key: value for key, value in event.items() if key != "label"}
                elif kind in ("started", "stats"):
                    setattr(self, kind, event)
                elif kind == "error":
                    self.error = event["message"]
                elif kind == "log":
                    msg = event["message"]
                    if "audioPeak" in msg:
                        match = re.search(r'\{.*\}', msg)
                        if match:
                            self.share_stats.append(json.loads(match.group()))
                    self.logs = (self.logs + [msg])[-30:]
        except Exception as exc:
            self.error = repr(exc)

    def check(self):
        if self.error or self.proc.poll() is not None:
            raise RuntimeError(f"tvpipe exited {self.proc.poll()}: {self.error}; {self.logs}")
        if self.receiver and self.receiver.error:
            raise RuntimeError(self.receiver.error)

    def stop(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(record(6, 0, b'{"type":"stop"}'))
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.proc.wait()
        self.thread.join(15)
        if self.receiver:
            self.receiver.close()


def proc_sample():
    """The container holds one Chromium instance. Include its forked helpers."""
    procs = {}
    for p in Path("/proc").glob("[0-9]*"):
        try:
            name, fields = (p / "stat").read_text().rsplit(")", 1)
            f = fields.split()
            procs[int(p.name)] = (name.split("(", 1)[1], int(f[1]), int(f[11])+int(f[12]), int(f[19]))
        except FileNotFoundError:
            continue
    browser = {pid for pid, (name, *_) in procs.items() if name.startswith(("chromium", "chrome_crashpad"))}
    while True:
        children = {pid for pid, (_, parent, *_) in procs.items() if parent in browser}
        if children <= browser:
            break
        browser.update(children)
    ticks = {}
    memory = {k: [0, 0] for k in ("browser", "tvpipe", "harness")}
    for pid, (name, parent, cpu, birth) in procs.items():
        group = "browser" if pid in browser else "tvpipe" if name == "tvpipe" else "harness" if pid == os.getpid() else None
        if not group:
            continue
        ticks[(pid, birth)] = (group, cpu)
        try:
            lines = Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
        except (FileNotFoundError, ProcessLookupError):
            continue
        for line in lines:
            field, *rest = line.split()
            if field in ("Rss:", "Pss:"):
                memory[group][field == "Pss:"] += int(rest[0]) / 1024
    return ticks, memory


def measure(sender, seconds):
    deadline = time.monotonic() + 30
    while not (sender.receiver and sender.receiver.video > 2):
        sender.check()
        if time.monotonic() >= deadline:
            raise RuntimeError(f"no H.264 at receiver: {sender.logs}")
        time.sleep(.1)
    time.sleep(3)
    receiver = sender.receiver
    first_video, first_audio = receiver.video, receiver.audio
    prev, peak = proc_sample()
    ticks = dict.fromkeys(peak, 0)
    start = time.monotonic()
    sender.share_stats.clear()
    while time.monotonic() < start + seconds:
        time.sleep(min(1, max(0, start + seconds - time.monotonic())))
        sender.check()
        cur, mem = proc_sample()
        for key, (group, now) in cur.items():
            ticks[group] += now - prev.get(key, (group, 0))[1]
        for group, (rss, pss) in mem.items():
            peak[group] = [max(peak[group][0], rss), max(peak[group][1], pss)]
        prev = cur
    elapsed = time.monotonic() - start
    report = {
        "seconds": round(elapsed, 3), "received_video": receiver.video - first_video,
        "cgroup_peak_mib": round(int(Path("/sys/fs/cgroup/memory.peak").read_text()) / 2**20, 1) if Path("/sys/fs/cgroup/memory.peak").exists() else None,
        "received_fps": round((receiver.video-first_video)/elapsed, 2),
        "received_audio_records": receiver.audio-first_audio,
        "pcm_peak": round(receiver.peak/32768, 3),
        "tone_hz": round(receiver.crossings * receiver.config["audio"]["sampleRate"] / receiver.samples, 1) if receiver.samples else None,
        "keyframes_including_warmup": receiver.keys,
        "cpu_pct": {g: round(n / CLK / elapsed * 100, 2) for g, n in ticks.items()},
        "peak_rss_pss_mib": {g: [round(v, 1) for v in pair] for g, pair in peak.items()},
        "capture": sender.capture, "started": sender.started, "stats": sender.stats,
        "receiver_video_config": receiver.config["video"],
    }
    if sender.share_stats:
        for field in ("fps", "sourceFps", "audioPeak", "audioKbps", "encodeMs", "dropped", "repeated"):
            vals = [row[field] for row in sender.share_stats if field in row]
            if vals:
                report[field + "_min_mean_max"] = [min(vals), round(sum(vals)/len(vals), 2), max(vals)]
    return report


class CDP:
    def __init__(self, url, profile, size):
        w, h = size.split("x")
        self.chrome = subprocess.Popen(["s2-chromium", "--headless=new", "--remote-debugging-port=0",
                                       f"--user-data-dir={profile}", f"--window-size={w},{h}",
                                       f"--screen-info={{0,0 {w}x{h}}}", "--force-device-scale-factor=1",
                                       "--autoplay-policy=no-user-gesture-required", url],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.id = 0
        self.ws = None
        try:
            portfile = Path(profile) / "DevToolsActivePort"
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                if portfile.exists():
                    port = portfile.read_text().splitlines()[0]
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                        tabs = json.load(response)
                    pages = [t for t in tabs if t["type"] == "page" and t["url"] == url]
                    if pages:
                        break
                time.sleep(.1)
            else:
                raise RuntimeError("Chromium did not open the test page")
            self.ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
            while time.monotonic() < deadline:
                ready = self.call("Runtime.evaluate", {"expression": "document.title === 'Render spike' && document.readyState === 'complete'"})
                if ready["result"].get("value"):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("test page did not finish loading")
            self.call("Emulation.setDeviceMetricsOverride", {"width":int(w), "height":int(h), "deviceScaleFactor":1, "mobile":False})
        except BaseException:
            self.close()
            raise

    def send(self, method, params):
        self.id += 1
        self.ws.send(json.dumps(dict(id=self.id, method=method, params=params)))
        return self.id

    def call(self, method, params):
        req = self.send(method, params)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == req:
                if "error" in msg:
                    raise RuntimeError(msg)
                return msg["result"]

    def close(self):
        if self.ws:
            self.ws.close()
        self.chrome.terminate()
        try:
            self.chrome.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.chrome.kill()
            self.chrome.wait()


def configs(cdp):
    result = []
    for w, h, codec in [(1280,720,"42E01F"), (1920,1080,"42E028"), (1920,1080,"4D4028"), (1920,1080,"640028")]:
        cfg = dict(codec="avc1."+codec,width=w,height=h,framerate=30,bitrate=2500000,latencyMode="realtime",avc={"format":"avc"})
        expr = f"VideoEncoder.isConfigSupported({json.dumps(cfg)})"
        value = cdp.call("Runtime.evaluate", dict(expression=expr, awaitPromise=True, returnByValue=True))
        if "exceptionDetails" in value:
            raise RuntimeError(value["exceptionDetails"])
        result.append(value["result"]["value"])
    return result


def fallback(cdp, sender, stop, errors):
    try:
        if not sender.connected.wait(15):
            raise RuntimeError("raw input did not open")
        cdp.call("Page.startScreencast", dict(format="jpeg", quality=75, maxWidth=1280, maxHeight=720, everyNthFrame=1))
        # One outstanding CDP frame. Delay the ACK until the 10 Hz sampling deadline.
        # Chromium can have multiple events queued during the ACK delay.
        start = time.monotonic()
        due = start
        while not stop.is_set():
            event = json.loads(cdp.ws.recv())
            if event.get("method") != "Page.screencastFrame":
                continue
            frame = event["params"]
            if stop.wait(max(0, due - time.monotonic())):
                break
            with Image.open(io.BytesIO(base64.b64decode(frame["data"]))) as image:
                if image.size != (1280, 720):
                    raise RuntimeError(f"unexpected JPEG size {image.size}")
                data = image.convert("RGB").tobytes()
            now = time.monotonic()
            sender.proc.stdin.write(record(1, int((now-start)*1e6), data))
            sender.proc.stdin.flush()
            due = max(due + .1, now)
            cdp.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})
    except Exception as exc:
        errors.append(repr(exc))
        sender.error = repr(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["configs","share","fallback"])
    parser.add_argument("--page", choices=["static","slides","animation"], default="animation")
    parser.add_argument("--size", choices=["1280x720","1920x1080"], default="1280x720")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--label", default="native")
    args = parser.parse_args()
    os.chdir(HERE)
    with stage(args.page) as url, tempfile.TemporaryDirectory() as profile, contextlib.ExitStack() as stack:
        if args.mode != "share":
            cdp = CDP(url, profile, args.size)
            stack.callback(cdp.close)
            if args.mode == "configs":
                print(json.dumps(configs(cdp)))
                return
        opts = ["--to", "browser", "--port", "0", "--size", args.size, "--bitrate", "2500"]
        if args.mode == "share":
            sender = Sender(["share", url, *opts, "--fps", str(args.fps), "--browser", "/usr/local/bin/s2-chromium"])
        else:
            if args.size != "1280x720":
                raise ValueError("fallback runs at 1280x720")
            args.fps = 10
            sender = Sender(["play", *opts, "--fps", "10", "--encoder", "builtin", "--raw-video", "1280x720@10:rgb24", "-"])
        stack.callback(sender.stop)
        if args.mode == "fallback":
            stop, errors = threading.Event(), []
            producer = threading.Thread(target=fallback, args=(cdp, sender, stop, errors))
            producer.start()
            stack.callback(producer.join, 12)
            stack.callback(stop.set)
        result = measure(sender, args.seconds)
    if sender.proc.returncode or sender.error:
        raise RuntimeError(f"tvpipe exit {sender.proc.returncode}: {sender.error}")
    print(json.dumps({**vars(args), **result}))


if __name__ == "__main__":
    main()
