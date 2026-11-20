"""Minimal pixel exporters. Stdout is a length-prefixed frame pipe."""
import array
import os
import struct
import sys
import threading

WIDTH, HEIGHT = 1920, 1080
engine, url = sys.argv[1:]
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
os.environ["WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"] = "1"


def send(payload):
    sys.stdout.buffer.write(struct.pack("<I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


if engine.startswith("wpe"):
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    pipeline = Gst.parse_launch(
        "wpesrc name=source draw-background=true ! "
        f"video/x-raw,format=BGRA,width={WIDTH},height={HEIGHT},framerate=10/1 ! "
        "appsink name=sink max-buffers=1 drop=true sync=false"
    )
    source = pipeline.get_by_name("source")
    source.set_property("location", url)
    sink = pipeline.get_by_name("sink")
    pcm = Gst.parse_bin_from_description(
        "queue ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=48000,channels=2 ! "
        "appsink name=pcm emit-signals=true sync=false", True
    )
    audio_found = False

    def pcm_sample(output):
        global audio_found
        sample = output.emit("pull-sample")
        if not audio_found:
            buffer = sample.get_buffer()
            values = array.array("h", buffer.extract_dup(0, buffer.get_size()))
            peak = max((abs(v) for v in values), default=0)
            if peak:
                print(f"WPE PCM peak {peak} / 32768 (stereo 48 kHz s16le)", file=sys.stderr, flush=True)
                audio_found = True
        return Gst.FlowReturn.OK

    def link_audio(_source, pad):
        if pad.get_name().startswith("audio_") and not pcm.get_parent():
            pipeline.add(pcm)
            pad.link(pcm.get_static_pad("sink"))
            pcm.sync_state_with_parent()

    pcm.get_by_name("pcm").connect("new-sample", pcm_sample)
    source.connect("pad-added", link_audio)
    pipeline.set_state(Gst.State.PLAYING)
    try:
        for _ in sys.stdin:
            sample = sink.emit("try-pull-sample", 4 * Gst.SECOND)
            if sample:
                buffer = sample.get_buffer()
                send(buffer.extract_dup(0, buffer.get_size()))
            else:
                error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                if error:
                    raise RuntimeError(error.parse_error())
                send(b"")
    finally:
        pipeline.set_state(Gst.State.NULL)
elif engine == "gtk":
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    gi.require_foreign("cairo")
    from gi.repository import GLib, Gtk, WebKit2
    import io

    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_property("media-playback-requires-user-gesture", False)
    window = Gtk.Window()
    window.set_decorated(False)
    window.set_default_size(WIDTH, HEIGHT)
    window.add(view)
    window.show_all()
    view.load_uri(url)

    def capture():
        def done(widget, result):
            try:
                image = widget.get_snapshot_finish(result)
                output = io.BytesIO()
                image.write_to_png(output)
                send(output.getvalue())
            except GLib.Error:
                send(b"")
        view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, done)
        return False

    def commands():
        for _ in sys.stdin:
            GLib.idle_add(capture)
        GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=commands, daemon=True).start()
    Gtk.main()
elif engine == "qt":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --autoplay-policy=no-user-gesture-required"
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QTimer, QUrl
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication

    app = QApplication(["s2b-webengine"])
    target = QWebEngineView()
    target.resize(WIDTH, HEIGHT)
    target.show()
    target.load(QUrl(url))
    # Dispatch input on the GUI thread while the browser continues animating.
    import queue

    pending = queue.SimpleQueue()

    def commands():
        for _ in sys.stdin:
            pending.put(True)
        pending.put(False)

    def service_input():
        if pending.empty():
            return
        if not pending.get_nowait():
            app.quit()
            return
        image = QByteArray()
        buffer = QBuffer(image)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        target.grab().save(buffer, "PNG")
        send(bytes(image))

    threading.Thread(target=commands, daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(service_input)
    timer.start(5)
    app.exec()
