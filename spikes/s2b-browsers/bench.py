"""One browser and workload per native ARM64 container. All I/O is loopback."""
import argparse
import base64
import contextlib
import hashlib
import http.server
import io
import json
import math
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import wave

from PIL import Image
import websocket

S2 = Path("/bench/spikes/s2-render")
sys.path.insert(0, str(S2))
from bench import Sender, record

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1920, 1080
SIZE = (WIDTH, HEIGHT)
FREQ = os.sysconf("SC_CLK_TCK")
CHROMIUM = {"chromium": "/usr/bin/chromium", "shell": "/usr/bin/chromium-headless-shell"}
AGENTS = {"kitesurf", "obscura", "moli"}
EMBEDDED = {"wpe", "wpe54", "wpe-audio", "gtk", "qt", "ultralight"}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        route = parsed.path
        if route == "/observed":
            self.server.observed.append({key: values[0] for key, values in params.items()})
            self.respond(b"ok", "text/plain")
            return
        if route == "/tone.wav":
            self.respond(self.server.tone, "audio/wav")
            return
        if route == "/blank":
            self.respond(b"<!doctype html><title>S2b blank</title>", "text/html")
            return
        path = {
            "/stage": HERE / "stage.html",
            "/recipe": S2 / "stage.html",
            "/chart": HERE / "apps.html",
            "/step": HERE / "apps.html",
            "/probe": HERE / "probe.html",
        }.get(route)
        if path is None:
            self.send_error(404)
            return
        data = path.read_text()
        if route == "/recipe":
            # Keep the original S2 visuals. Audio has an independent <audio> probe.
            data = data.split("const ctx = new AudioContext();")[0]
            if params.get("freeze") and params.get("page") == ["slides"]:
                data = data.replace('id="second"', 'id="second" class="shown"')
            data += "</script>"
        self.respond(data.encode(), "text/html; charset=utf-8")

    def log_message(self, *args):
        pass

    def respond(self, payload, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def tone():
    file = io.BytesIO()
    with wave.open(file, "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * math.tau * 440 / 48000)))
                                 for i in range(48000)))
    return file.getvalue()


@contextlib.contextmanager
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.observed = []
    httpd.tone = tone()
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    try:
        yield httpd, f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def json_request(url, data=None, method=None):
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"}, method=method)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def wait_http(url, proc, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return json_request(url)
        except (URLError, ValueError, ConnectionError):
            if proc.poll() is not None:
                raise RuntimeError(f"browser exited {proc.returncode}")
            time.sleep(.1)
    raise RuntimeError(f"unavailable: {url}")


def executable(name):
    return str(next(p for p in Path("/opt/engine").rglob(name) if p.is_file()))


class CDP:
    method = "CDP screencast"

    def __init__(self, engine, proc, endpoint):
        self.engine = engine
        self.id = 0
        self.session = None
        self.events = []
        if engine in CHROMIUM:
            targets = wait_http(endpoint + "/json/list", proc)
            target = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
            self.ws = websocket.create_connection(target, suppress_origin=True, timeout=10)
        else:
            version = wait_http(endpoint + "/json/version", proc)
            target = version.get("webSocketDebuggerUrl", endpoint.replace("http:", "ws:") + "/devtools/browser")
            self.ws = websocket.create_connection(target,
                                                  suppress_origin=True, timeout=10)
            target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Page.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False})

    def send(self, method, params):
        self.id += 1
        message = dict(id=self.id, method=method, params=params)
        if self.session:
            message["sessionId"] = self.session
        self.ws.send(json.dumps(message))
        return self.id

    def call(self, method, params=None):
        seq = self.send(method, params or {})
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == seq:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})

    def capture(self):
        shot = self.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        return base64.b64decode(shot["data"])

    def start(self):
        self.call("Page.startScreencast", dict(format="jpeg", quality=75,
                                              maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=1))
        self.ws.settimeout(.2)

    def next_frame(self, deadline):
        event = None
        while time.monotonic() < deadline:
            if self.events:
                message = self.events.pop(0)
            else:
                self.ws.settimeout(min(.1, max(.001, deadline - time.monotonic())))
                try:
                    message = json.loads(self.ws.recv())
                except websocket.WebSocketTimeoutException:
                    break
            if message.get("method") == "Page.screencastFrame":
                event = message["params"]
                break
        if event:
            return base64.b64decode(event["data"]), event["sessionId"]
        return None

    def ack(self, token):
        self.send("Page.screencastFrameAck", {"sessionId": token})

    def stop(self):
        self.ws.settimeout(10)
        self.call("Page.stopScreencast")

    def close(self):
        self.ws.close()


class WebDriver:
    method = "WebDriver PNG"

    def __init__(self, engine, proc, endpoint):
        self.endpoint = endpoint
        wait_http(endpoint + "/status", proc)
        capabilities = {"alwaysMatch": {"acceptInsecureCerts": True}}
        if engine == "geckodriver":
            capabilities["alwaysMatch"]["moz:firefoxOptions"] = {
                "args": ["-headless", "--width=1920", "--height=1080"],
                "prefs": {"media.autoplay.default": 0, "layout.css.devPixelsPerPx": "1.0"}
            }
        if engine in {"servo", "ladybird"}:
            capabilities["alwaysMatch"].pop("acceptInsecureCerts")
        if engine == "ladybird":
            capabilities["alwaysMatch"]["ladybird:headless"] = True
        reply = json_request(endpoint + "/session", {"capabilities": capabilities})
        self.endpoint += "/session/" + reply["value"]["sessionId"]
        if engine == "geckodriver":
            json_request(self.endpoint + "/window/rect", {"width": 1920, "height": 1080})
            gaps = json_request(self.endpoint + "/execute/sync", {
                "script": "return [outerWidth-innerWidth,outerHeight-innerHeight]", "args": []})["value"]
            json_request(self.endpoint + "/window/rect", {"width": 1920 + gaps[0], "height": 1080 + gaps[1]})
        if engine in {"ladybird", "servo"}:
            json_request(self.endpoint + "/window/rect", {"width": 1920, "height": 1080})

    def navigate(self, url):
        json_request(self.endpoint + "/url", {"url": url})

    def capture(self):
        return base64.b64decode(json_request(self.endpoint + "/screenshot")["value"])

    def close(self):
        with contextlib.suppress(URLError):
            json_request(self.endpoint, method="DELETE")


class Embedded:
    def __init__(self, engine, launch):
        self.engine = engine
        self.launch = launch
        self.proc = None
        self.method = "GStreamer BGRA" if engine.startswith("wpe") else {
            "gtk": "WebKit GTK PNG", "qt": "Qt widget PNG", "ultralight": "BitmapSurface BGRA"
        }[engine]

    def navigate(self, url):
        self.close()
        cmd = [sys.executable, str(HERE / "embedded.py"), self.engine, url]
        if self.engine == "ultralight":
            cmd = ["/opt/ultralight/frame", url]
        elif not self.engine.startswith("wpe"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", *cmd]
        self.proc = self.launch(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def capture(self):
        self.proc.stdin.write(b"frame\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.read(4)
        if len(header) != 4:
            raise RuntimeError("frame pipe closed")
        size, = struct.unpack("<I", header)
        return self.proc.stdout.read(size)

    def close(self):
        if self.proc is not None:
            stop_process(self.proc)
            self.proc = None


def stop_process(proc):
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


@contextlib.contextmanager
def open_engine(engine, runlog, profile, capture):
    processes = []

    def launch(cmd, **kwargs):
        proc = subprocess.Popen(cmd, start_new_session=True, stderr=runlog, **kwargs)
        processes.append(proc)
        return proc

    browser = None
    try:
        p = port()
        endpoint = f"http://127.0.0.1:{p}"
        if engine in CHROMIUM:
            proc = launch([CHROMIUM[engine], "--no-sandbox", "--headless=new",
                           "--disable-dev-shm-usage", "--disable-background-networking",
                           "--no-first-run", f"--user-data-dir={profile}",
                           f"--remote-debugging-port={p}", "--window-size=1920,1080",
                           "--screen-info={0,0 1920x1080}", "--force-device-scale-factor=1",
                           "--autoplay-policy=no-user-gesture-required", "about:blank"], stdout=runlog)
            browser = CDP(engine, proc, endpoint)
        elif engine in AGENTS:
            if engine == "kitesurf":
                proc = launch(["/opt/kitesurf/entrypoint.sh"], stdout=runlog,
                              env=os.environ | {"KITESURF_PORT": str(p)})
            elif engine == "moli":
                proc = launch([executable("moli"), "serve", "--port", str(p), "--layout", "--resource",
                               "--screencast-interval-ms", "100"], stdout=runlog)
            else:
                proc = launch([executable("obscura"), "serve", "--port", str(p), "--allow-private-network"],
                              stdout=runlog)
            browser = CDP(engine, proc, endpoint)
        elif engine in {"geckodriver", "servo", "ladybird"}:
            if engine == "geckodriver":
                cmd = [executable("geckodriver"), "--port", str(p)]
            elif engine == "ladybird":
                cmd = ["/opt/ladybird/bin/WebDriver", "--headless", "--disable-sandbox",
                       "--force-cpu-painting", "--listen-address", "127.0.0.1", "--port", str(p)]
            else:
                cmd = [executable("servoshell"), "--headless", "--screen-size", "1920x1080",
                       "--webdriver", str(p), "1920x1080"]
            proc = launch(cmd, stdout=runlog,
                          env=os.environ | {"LIBGL_ALWAYS_SOFTWARE": "1",
                                            "MOZ_HEADLESS_WIDTH": "1920", "MOZ_HEADLESS_HEIGHT": "1080"})
            browser = WebDriver(engine, proc, endpoint)
        elif engine in EMBEDDED:
            browser = Embedded(engine, launch)
        if capture == "poll" and isinstance(browser, CDP):
            browser.method = "CDP PNG"
        yield browser
    finally:
        try:
            if browser:
                browser.close()
        finally:
            for proc in reversed(processes):
                stop_process(proc)


def cgroup_stats():
    path = Path("/sys/fs/cgroup/cpu.stat")
    return {k: int(v) for k, v in map(str.split, path.read_text().splitlines())}


def sample():
    """Count all container processes except tini and the benchmark itself."""
    result = {"engine": [0.0, 0.0, 0], "harness": [0.0, 0.0, 0], "tvpipe": [0.0, 0.0, 0]}
    for path in Path("/proc").glob("[0-9]*"):
        pid = int(path.name)
        if pid == 1:
            continue
        try:
            name, rest = (path / "stat").read_text().rsplit(")", 1)
            fields = rest.split()
            group = "harness" if pid == os.getpid() else "tvpipe" if name.endswith("(tvpipe") else "engine"
            result[group][2] += int(fields[11]) + int(fields[12])
            for line in (path / "smaps_rollup").read_text().splitlines():
                field, *parts = line.split()
                if field in {"Rss:", "Pss:"}:
                    result[group][field == "Pss:"] += int(parts[0]) / 1024
        except (OSError, ProcessLookupError):
            continue
    return result


def decode(payload, raw=False):
    if raw:
        return Image.frombytes("RGBA", SIZE, payload, "raw", "BGRA")
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        return image.copy()


def measure(browser, args, sender):
    streaming = browser.method == "CDP screencast"
    raw = browser.method.endswith("BGRA")
    interval = 1 / args.rate
    if streaming:
        browser.start()
    start = due = time.monotonic()
    end = start + args.seconds
    cgroup_start = cgroup_stats()
    initial = peak = sample()
    next_sample = start + 1
    received_start = sender.receiver.video if sender else 0
    count = unique = wrong_size = 0
    changes = []
    max_gap = 0
    seen_time = start
    before = last_pts = last_send = None
    last_image = None
    while time.monotonic() < end:
        now = time.monotonic()
        if streaming:
            event = browser.next_frame(min(end, max(due, now) + .1))
        else:
            event = (browser.capture(), None)
        if event and event[0]:
            payload, token = event
            image = None if raw else decode(payload)
            correct_size = len(payload) == WIDTH * HEIGHT * 4 if raw else image.size == SIZE
            if correct_size:
                pixels = payload if raw or streaming else image.tobytes()
                current = hashlib.blake2s(pixels).digest()
                count += 1
                if current != before:
                    unique += 1
                    before = current
                    now = time.monotonic()
                    max_gap = max(max_gap, now - seen_time)
                    seen_time = now
                    changes.append(now - start)
                last_image = payload if raw else image
                now = time.monotonic()
                if sender and (last_send is None or (now - last_send) >= .32 or current != last_pts):
                    pts = int((now - start) * 1_000_000)
                    sender.proc.stdin.write(record(1, pts, payload if raw else image.convert("RGB").tobytes()))
                    sender.proc.stdin.flush()
                    last_pts = current
                    last_send = now
            else:
                wrong_size += 1
            if streaming:
                time.sleep(max(0, due - time.monotonic()))
                browser.ack(token)
        elif sender and last_image is not None and time.monotonic() - last_send >= .32:
            now = time.monotonic()
            pixels = last_image if raw else last_image.convert("RGB").tobytes()
            sender.proc.stdin.write(record(1, int((now - start) * 1_000_000), pixels))
            sender.proc.stdin.flush()
            last_send = now
        now = time.monotonic()
        if now >= next_sample:
            values = sample()
            peak = {key: [max(a, b) for a, b in zip(peak[key], row)] for key, row in values.items()}
            next_sample = now + 1
        if sender:
            sender.check()
        due = max(due + interval, now)
        time.sleep(max(0, min(due, end) - time.monotonic()))
    elapsed = time.monotonic() - start
    end_cpu = cgroup_stats()
    final = sample()
    if streaming:
        browser.stop()
    memory = {g: [round(max(a, b), 1) for a, b in zip(row[:2], final[g][:2])]
              for g, row in peak.items()}
    return {
        "mode": browser.method, "seconds": round(elapsed, 3), "frames": count,
        "target_fps": args.rate,
        "distinct_frames": unique, "wrong_size": wrong_size,
        "out_fps": round(count / elapsed, 2), "distinct_fps": round(unique / elapsed, 2),
        "peak_distinct_in_one_second": max(
            (sum(t <= later < t + 1 for later in changes) for t in changes), default=0
        ),
        "max_interchange_s": round(max(max_gap, start + elapsed - seen_time), 2),
        "engine_peak_rss_pss_mib": memory["engine"],
        "harness_peak_rss_pss_mib": memory["harness"],
        "tvpipe_peak_rss_pss_mib": memory["tvpipe"],
        "cpu_pct": {g: round((final[g][2] - initial[g][2]) / FREQ / elapsed * 100, 2) for g in final},
        "cpu_cgroup_pct": round((end_cpu["usage_usec"] - cgroup_start["usage_usec"]) / 1e4 / elapsed, 2),
        "throttled_periods": end_cpu["nr_throttled"] - cgroup_start["nr_throttled"],
        "h264_fps": round((sender.receiver.video - received_start) / elapsed, 2) if sender else None,
        "receiver_video_config": sender.receiver.config.get("video") if sender else None,
    }


@contextlib.contextmanager
def audio_sink(enabled):
    if not enabled:
        yield
        return
    sock = "/tmp/s2b-pulse"
    server = subprocess.Popen(["pulseaudio", "--system", "--daemonize=no", "--disallow-exit",
                               "--disable-shm", "--exit-idle-time=-1", "-n",
                               "-L", f"module-native-protocol-unix socket={sock} auth-anonymous=1",
                               "-L", "module-null-sink sink_name=s2b"], start_new_session=True)
    try:
        end = time.monotonic() + 8
        while not Path(sock).exists():
            if server.poll() is not None or time.monotonic() >= end:
                raise RuntimeError("PulseAudio null sink did not start")
            time.sleep(.05)
        os.environ["PULSE_SERVER"] = f"unix:{sock}"
        try:
            yield
        finally:
            del os.environ["PULSE_SERVER"]
    finally:
        stop_process(server)


def measure_share(root, args):
    path = {
        "chromium": str(HERE / "chromium-wrapper"),
        "shell": str(HERE / "shell-wrapper"),
        "geckodriver": "/usr/bin/firefox-esr",
        "firefox-av": "/usr/bin/firefox-esr",
    }[args.engine]
    page, seconds = args.page, args.seconds
    sender = Sender(["share", f"{root}/stage?page={page}", "--to", "browser", "--port", "0",
                     "--browser", path, "--size", "1920x1080",
                     "--fps", "10", "--bitrate", "4000"])
    try:
        deadline = time.monotonic() + 20
        while not (sender.receiver and sender.receiver.video > 2):
            sender.check()
            if time.monotonic() > deadline:
                raise RuntimeError(f"No H.264: {sender.logs}")
            time.sleep(.05)
        time.sleep(1)
        baseline = sample()
        peak = baseline["engine"][:2]
        cpu = cgroup_stats()["usage_usec"]
        began = time.monotonic()
        video, audio = sender.receiver.video, sender.receiver.audio
        while time.monotonic() < began + seconds:
            time.sleep(min(1, max(0, began + seconds - time.monotonic())))
            sender.check()
            current = sample()
            peak = [max(a, b) for a, b in zip(peak, current["engine"])]
        elapsed = time.monotonic() - began
        return {
            "engine": args.engine, "mode": "tvpipe share", "page": page,
            "seconds": round(elapsed, 3),
            "h264_fps": round((sender.receiver.video - video) / elapsed, 2),
            "pcm_records": sender.receiver.audio - audio,
            "reported_audio_capture": sender.capture,
            "peak_share_audio_level": max((s.get("audioPeak", 0) for s in sender.share_stats), default=0),
            "engine_peak_rss_pss_mib": [round(n, 1) for n in peak],
            "tvpipe_end_pss_mib": round(current["tvpipe"][1], 1),
            "cpu_pct": {g: round((current[g][2] - baseline[g][2]) / FREQ / elapsed * 100, 2)
                        for g in current},
            "cpu_cgroup_pct": round((cgroup_stats()["usage_usec"] - cpu) / 1e4 / elapsed, 2),
        }
    finally:
        sender.stop()


def save_still(browser, url, path):
    browser.navigate(url)
    time.sleep(1.5)
    raw = browser.method.endswith("BGRA")
    for _ in range(3):
        payload = browser.capture()
        if payload:
            image = decode(payload, raw)
            if image.size != SIZE:
                raise RuntimeError(f"snapshot dimensions: {image.size}")
            image.convert("RGB").save(path)
            return
    raise RuntimeError("no PNG")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine")
    parser.add_argument("page", choices=["static", "slides", "chart", "step", "probe", "animation"])
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--rate", type=float, default=10, help="maximum capture polls or CDP ACKs per second")
    parser.add_argument("--capture", choices=["poll", "stream"], default="stream")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--direct-page", action="store_true")
    args = parser.parse_args()
    if args.seconds <= 0 or args.rate <= 0:
        parser.error("duration and capture rate must be positive")
    if args.share:
        with server() as (_, root), audio_sink(args.engine == "firefox-av"):
            print(json.dumps(measure_share(root, args)))
        return
    label = f"{args.engine}-{args.page}" + ("-raw" if args.raw else "")
    if args.direct_page:
        label += "-direct"
    output = Path("/out")
    with server() as (httpd, root), tempfile.TemporaryDirectory() as profile, \
            (output / f"{label}.log").open("wb") as runlog, \
            open_engine(args.engine, runlog, profile, args.capture) as browser:
        browser.navigate(root + "/blank")
        time.sleep(1.5)
        idle = sample()["engine"][:2]
        if args.page == "probe":
            browser.navigate(root + "/probe")
            time.sleep(3.5)
            observations = [json.loads(row["probe"]) for row in httpd.observed if "probe" in row]
            print(json.dumps({"engine": args.engine, "probe": observations}))
            return
        route = "/stage"
        if args.direct_page or args.page == "animation":
            route = "/recipe" if args.page in {"static", "slides", "animation"} else "/" + args.page
        url = f"{root}{route}?page={args.page}"
        browser.navigate(url)
        time.sleep(1.0)
        sender = None
        try:
            if args.raw:
                pixel_format = "bgra" if browser.method.endswith("BGRA") else "rgb24"
                sender = Sender(["play", "--to", "browser", "--port", "0", "--raw-video",
                                 f"1920x1080@10:{pixel_format}", "--encoder", "builtin", "--bitrate", "4000", "-"])
                if not sender.connected.wait(12):
                    raise RuntimeError("tvpipe did not connect")
            result = measure(browser, args, sender)
            result.update(engine=args.engine, page=args.page, raw=args.raw, idle_rss_pss_mib=idle,
                          step_events=[int(row["step"]) for row in httpd.observed if "step" in row])
            print(json.dumps(result), flush=True)
        finally:
            if sender:
                sender.stop()
        if args.page != "animation" and not args.raw:
            save_still(browser, url + "&freeze=1", output / f"{label}.png")


if __name__ == "__main__":
    main()
