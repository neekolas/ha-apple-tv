# Passing Siri Remote presses through to a mirrored page

**Question.** `tv_show` mirrors a browser page to the Apple TV through tvpipe. Can the bridge learn about each press on the Siri Remote during that mirror, so it can forward left, right, up, down and select into the page as key events?

**Status: source review only. Verdict: pending hardware probe.** No path below has been tested on hardware for this question.

Source references use bridge checkout `7a5e37b`. tvpipe references use tag `v0.2.0`, commit `c2456d29`. pymobiledevice3 references use version 11.61.0, the version locked in `app/uv.lock`. A claim marked *(speculation)* has no source behind it.

## Verdict

TBD

## Pass bar

This bar was set before any hardware result. A usable signal must meet all five points.

1. **Key identity.** It identifies which key was pressed.
2. **Shared process.** It comes from a process that the physical Siri Remote also goes through. A signal that exists only on the Companion path used by `tv_remote` fails.
3. **Coverage.** It fires on nearly every press.
4. **Latency.** It arrives within a few hundred milliseconds.
5. **No self-trigger.** The bridge's own screen reads do not trigger it.

## Paths

Paths that work during a mirror come first. Within that group the order is how many of the five keys the path could identify, then build cost. Paths that replace the mirror or carry no press come last. The order is a hypothesis until the probe runs.

| # | Path | Keys it could deliver | Feasibility | Latency | Reliability | Build cost | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | os_trace syslog relay over RSD | All five, if a shared process logs a line per press with a readable key | Medium. Service not yet confirmed on the probe TV | Tens of ms plus tunnel, expected | `<private>` redaction may hide the key. Log lines change between tvOS versions | Small | `source-only` |
| 2 | DVT `activitytracetap` | Same as path 1, plus format string and typed arguments | Medium. DVT already works for screenshots | Batched. Floor near 200 ms, unverified | Same as path 1. Parser marked "less stable" upstream | Small to medium | `source-only` |
| 3 | DVT kdebug, IOHID class | A press with no key name. Arguments may map to a key *(speculation)* | Low to medium | About 200 ms floor | Likely differs between Companion and the physical Siri Remote | Medium | `source-only` |
| 4 | AirPlay 2 event channel, passive | Unknown. Observed command types are housekeeping only | Low for presses *(speculation)*. tvpipe already connects the channel | Under 50 ms | High if present. Ordered TCP, each command acknowledged | None. Run tvpipe with `-v` | `source-only` |
| 5 | AirPlay 2 event channel with advertised MR commands | Play/Pause most likely. Left and right only if the mirror UI maps them to skip *(speculation)*. No up, down or select | Medium for Play/Pause, low for left and right. Unknown for a mirror session | 50–150 ms | Unknown. Media commands are not guaranteed per press | 3–5 days. Fork tvpipe | `source-only` |
| 6 | MRP tunnel | Play/Pause as a state change. None of the five keys | Low to medium | 100–300 ms | Poor. A state change is not a press | Listen: none | `source-only` |
| 7 | Companion events | None directly | Very low | 100–300 ms | Poor | Listen: none | `source-only` |
| 8 | DACP/RAOP callback | Play/Pause, perhaps volume. None of the five keys | Low. Needs a second RAOP session that may end the mirror | 50–200 ms | Unknown | 1–2 days | `source-only` |
| 9 | URL playback seek inference (`play_url`) | Left and right as ±10 s seeks, select as Play/Pause. No up or down | Medium for those signals. **Replaces the mirror**, so it cannot drive a live page | Press seen in 0.1–0.5 s. Picture changes after seconds | Medium. Seeks are inferred | 3–5 days for a slideshow mode | `source-only` |
| 10 | AX audit notifications | None. Reports focus changes | Low | n/a | A fullscreen mirror moves no focus | n/a | `source-only` |
| 11 | notification_proxy | None known. No payload before tvOS 27.2 | Low. Needs an exact notification name; none known | Low if a name existed | n/a | n/a | `source-only` |
| 12 | Other AirPlay-side: MRP HID or game-controller registration, CarPlay-style HID reports, Sidecar, MediaDataControl | None found | Very low | n/a | n/a | Do not build | `source-only` |
| 13 | Other RSD services: pcapd, BT packet logger, sysmontap, graphics, springboardservices | None | Very low | n/a | n/a | n/a | `source-only` |

Only paths 1–3 could name up, down and select. They are side channels, not documented events.

## Path details

### 1. os_trace syslog relay

- The service is `com.apple.os_trace_relay.shim.remote`. It pushes one record per log message with no client-side batching. Each record has pid, process path, sender image, level, subsystem, category, message and device time. See pymobiledevice3 `services/os_trace.py`.
- The client can request debug and info levels through `StreamFlags`. A `Pid` filter runs on the device and cuts volume.
- **It depends on a log line that may not exist.** No public source lists tvOS log lines for presses. The process list is a guess: `backboardd`, `PineBoard`, the AirPlay receiver UI, `mediaremoted`, `rapportd`, `bluetoothd` *(speculation)*.
- Arguments marked private arrive as `<private>`. Showing them needs a logging profile with approval on the device. Treat that as not available for a shipped bridge. See Apple developer forum threads [705810](https://developer.apple.com/forums/thread/705810) and [780682](https://developer.apple.com/forums/thread/780682). Integer arguments such as HID usage codes are often public, so a line may still name the key *(speculation)*.
- The service list for the probe TV is not recorded. The probe TV runs tvOS 26.6.1. The bridge supports tvOS 27 or later (`docs/live-accessibility.md:41`), so a line found on 26.6.1 must be confirmed again on a supported version.

### 2. DVT `activitytracetap`

- It reads the same log store as path 1, so it shows no line that path 1 lacks. Its gain is the format string, typed arguments and a device-side NSPredicate. See `services/dvt/instruments/activity_trace_tap.py`.
- Delivery is batched. pymobiledevice3 sets `ur: 500`. The kdebug tap documents `ur` as a flush interval in ms with a floor near 200 ms (`core_profile_session_tap.py`). That the activity tap has the same floor is an assumption. At 200 ms the path only just meets pass bar point 4.

### 3. DVT kdebug, IOHID class

- `coreprofilesessiontap` can filter kdebug by class. The macOS SDK lists `DBG_IOKIT 5` and `DBG_IOHID 35`. macOS `trace.codes` lists `HID_DispatchKeyboard`, `HID_HandleReport` and `HID_DispatchHIDEvent`.
- It is the only path that could see a press with no log line. It carries arguments, not a key name.
- Companion presses are injected in user space and may never reach kernel IOHID *(speculation)*. A null result with `tv_remote` does not rule this path out.

### 4 and 5. AirPlay 2 event channel

- The receiver returns `eventPort` in SETUP. tvpipe connects and answers each `POST /command` with `200 OK`. It knows `updateInfo`, `updateTimingPeerInfo` and `forceKeyFrame`. Any other type becomes `Event::Other` and is logged at debug level, type only. See tvpipe `crates/tvpipe/src/airplay/events.rs:29–56`, `session.rs:769–781`.
- **The tvpipe mirroring path has no remote-input callback.** It never sends a `/command` itself. doubletake behaves the same way (`internal/airplay/event_channel.go:268–335`).
- tvpipe's own hardware capture holds one event command, `updateInfo` (`crates/tvpipe/tests/vectors/airplay-mirror/appletv-session.txt:148`). That is tvpipe's observation. Nobody logged the channel while a person pressed keys.
- **A receiver-to-sender command exists in AirPlay 2.** shairport-sync 5.6 sends `sendMediaRemoteCommand` on the event channel after the sender posts `updateMRSupportedCommands`, `updateMRNowPlayingInfo` and `updateMRPlaybackState`. See shairport-sync `remote/remote.c:190–223`, `rtsp.c:2051–2069`. The project calls it "still a work in progress".
- The command numbers are media commands: play, pause, play/pause, stop, next, previous, fast forward, rewind (`remote/remote.c:33–48`). **No number stands for up, down, left, right or select.**
- The evidence is from audio sessions. Whether tvOS sends the command for a screen-mirroring session is unknown. Whether the mirror UI maps left and right to skip is unknown *(speculation: it does not)*.
- No Apple document was found that describes a way for an AirPlay sender to receive clickpad presses as raw button events. During video playback the Apple TV may treat a press as skip backward or forward. A native sender handles media commands through `MPRemoteCommandCenter`, and each press is not guaranteed to arrive ([forum thread 4036](https://developer.apple.com/forums/thread/4036)).

### 6. MRP tunnel

- The bridge already holds this tunnel when AirPlay credentials exist (`app/atv_bridge_app/atv/__init__.py:221–242`). pyatv is the MRP client and the TV is the origin. The TV pushes its state; pyatv sends commands.
- MRP defines `SEND_COMMAND_MESSAGE` with play, pause, skip and seek commands (`vendor/pyatv/pyatv/protocols/mrp/protobuf/CommandInfo.proto:5–62`). pyatv only creates this message to send it. It registers no handler for an inbound one.
- At best a Play/Pause press shows as a state change, and only if the mirror session is a now-playing client *(speculation)*.

### 7. Companion events

- The fork listens to `_iMC`, `SystemStatus`, `TVSystemStatus`, `_tiStarted` and `_tiStopped` (`vendor/pyatv/pyatv/protocols/companion/__init__.py:240–244, 434, 499–502, 584`). They report media flags and system state.
- `_hidC` (keys) and `_hidT` (touch) are requests from the client to the TV (`api.py:319, 329`). No source shows the TV sending physical remote input to a subscribed client.

### 8. DACP/RAOP

- The receiver calls `GET /ctrl-int/1/<cmd>` on a `_dacp._tcp` service that the sender advertises. Commands include `playpause`, `nextitem`, `volumeup` ([openairplay spec](https://openairplay.github.io/airplay-spec/audio/remote_control.html)). No command stands for a direction or select.
- pyatv sends the `DACP-ID` and `Active-Remote` headers but serves no callback (`vendor/pyatv/pyatv/support/rtsp.py:270–272`). tvpipe sends neither header in mirroring (tvpipe `docs/airplay-mirroring.md:285`).
- shairport-sync uses DACP for classic AirPlay and the event channel for AirPlay 2 (`remote/remote.c:544–565`). Whether a current tvOS receiver still browses `_dacp._tcp` is unknown *(speculation: unlikely)*.

### 9. URL playback seek inference (`play_url`)

- In this mode the tvOS player owns the remote. Left or right skips 10 seconds, the center plays or pauses, and up and down show controls ([Apple TV User Guide](https://support.apple.com/guide/tv/atvb7944597f/tvos)).
- The fork handles `playbackState` events on the event channel (`vendor/pyatv/pyatv/protocols/raop/protocols/airplayv2.py:197–217`). The sender could infer left and right from position jumps *(speculation)*.
- **A seek jump cannot be told apart from scrubbing or from another controller.** Up and down are invisible to the sender.
- `play_url` plays a rendered MP4 (`docs/ARCHITECTURE.md:68`). It cannot drive a live page. It may suit a slideshow with one slide per 10 s *(speculation)*.

### 10. AX audit notifications

- The host callbacks report focus, element and app changes. None is a key event. The callback list comes from iOS 18.6 simulator binaries, so it is indicative for tvOS, not proof.
- An earlier tvOS 27.2 run requested event mask 2. The daemon changed it back to 1 and gave no focus result after Back and Up (`docs/live-accessibility.md:14`). That run tested focus on Settings, not a mirror.
- The mirrored page is one video surface. A press there moves no tvOS focus, so no notification is expected.

### 11. notification_proxy

- `com.apple.mobile.notification_proxy.shim.remote` observes Darwin notifications by exact name only. No name that fires per press is known. Before tvOS 27.2 a notification carries no payload, so it gives no key identity there.

### 12 and 13. Other paths

- **MRP HID and game-controller messages** exist in `ProtocolMessage.proto:85–99`. pyatv only sends them to the TV. They let a client act as an input device *(speculation)*.
- **MediaDataControl** carries audio statistics (`raup`, `rrmt`) from the TV (tvpipe `mdc.rs:84–112`). **CarPlay-style HID reports** have no source. **Sidecar** is not offered by tvOS.
- **pcapd** sees IP packets. Bluetooth HID is not IP and Companion traffic is encrypted. **BT packet logger** has no RSD shim in pymobiledevice3 and needs a logging profile. **sysmontap, graphics and springboardservices** carry no input.

## Companion presses versus the physical remote

The hardware probe sends presses with `tv_remote`. That tool uses Companion `_hidC` (`vendor/pyatv/pyatv/protocols/companion/api.py:319`). The physical Siri Remote enters tvOS through a different path. No primary source for the tvOS internals was found, so this split is an assumption.

| Stage | Physical Siri Remote | Companion `tv_remote` |
| --- | --- | --- |
| Entry | Bluetooth LE → Bluetooth daemons → HID service | TCP Companion → `rapportd` and the TV remote daemon → virtual HID event |
| Kernel IOHID | Possible | Unlikely |
| `backboardd` dispatch, shell, focused app | Yes | Yes, assumed |

Effect on each path:

- **Paths 1 and 2.** A line from `rapportd` or a Companion daemon is a false positive under pass bar point 2. A line from `backboardd`, `PineBoard`, the AirPlay receiver UI or UIKit is the useful kind. A line from `bluetoothd` cannot appear with `tv_remote` at all.
- **Path 3.** Most likely to differ. See above.
- **Paths 4–9.** Not assessed. The sources do not say whether tvOS routes media commands the same way for both press sources.
- **Paths 10–13.** They carry no press from either source.

Two more limits on the probe:

- **Self-trigger.** `tv_remote` ends with one screen read (`docs/ARCHITECTURE.md:51`). So the bridge reads the screen on the TV right after each test press. A line that follows a press may come from that read. Run `tv_look` alone as a control to check pass bar point 5.
- **Mirror state.** Where tvOS sends presses during a fullscreen mirror is unknown. Back ends the mirror. Arrows and select may be dropped with no log line *(speculation)*. Run the probe with the mirror active.

Before any build decision, confirm the chosen signal once with a press on the physical Siri Remote. The probe script is `spikes/live/atvlab/press_probe.py`. It is uncommitted and only its syslog stream has run offline.

## Hardware results

TBD

## Sources

Bridge checkout:

- `docs/ARCHITECTURE.md:51, 62–68`, `docs/live-accessibility.md:14, 41`, `docs/pyatv-fork.md`
- `app/atv_bridge_app/atv/__init__.py:221–242`
- `vendor/pyatv/pyatv/protocols/companion/api.py:275–287, 319, 329`, `__init__.py:240–244, 434, 499–502, 584`
- `vendor/pyatv/pyatv/protocols/mrp/protobuf/ProtocolMessage.proto:85–99`, `CommandInfo.proto:5–62`, `messages.py:153–163`
- `vendor/pyatv/pyatv/protocols/raop/protocols/airplayv2.py:197–217`
- `vendor/pyatv/pyatv/support/rtsp.py:88–89, 270–272`
- `spikes/live/atvlab/press_probe.py` (uncommitted)

Other projects:

- [tvpipe v0.2.0](https://github.com/mitsuhiko/tvpipe/tree/v0.2.0), commit `c2456d29`: `docs/airplay-mirroring.md:285, 1205–1232`, `crates/tvpipe/src/airplay/events.rs`, `session.rs:769–781`, `mdc.rs`, `crates/tvpipe/tests/vectors/airplay-mirror/appletv-session.txt:148`
- [doubletake v0.6.0](https://github.com/omarroth/doubletake/tree/v0.6.0), commit `be27776b`: `internal/airplay/event_channel.go:268–335`
- [shairport-sync 5.6](https://github.com/mikebrady/shairport-sync/tree/5.6), commit `9118bbc`: `remote/remote.c:33–48, 190–223, 544–565`, `ap2_event_receiver.c:97–117`, `rtsp.c:2051–2069`. [Release notes](https://github.com/mikebrady/shairport-sync/releases)
- pymobiledevice3 11.61.0: `services/os_trace.py`, `services/notification_proxy.py`, `services/accessibilityaudit.py`, `services/pcapd.py`, `services/bt_packet_logger.py`, `services/dvt/instruments/activity_trace_tap.py`, `core_profile_session_tap.py`
- macOS SDK `usr/include/sys/kdebug.h`; `/usr/share/misc/trace.codes`
- Lab dumps of `axauditd` and `AccessibilityAudit` methods from iOS 18.6 simulator binaries (`/tmp/atvlab/`, not in the repository)

Web:

- [openairplay spec, Remote Control](https://openairplay.github.io/airplay-spec/audio/remote_control.html)
- [Apple TV User Guide, "Control what's playing"](https://support.apple.com/guide/tv/atvb7944597f/tvos)
- [Apple developer forums 4036](https://developer.apple.com/forums/thread/4036): presses not reaching `MPRemoteCommandCenter` during AirPlay
- Apple developer forums [705810](https://developer.apple.com/forums/thread/705810), [705868](https://developer.apple.com/forums/thread/705868), [780682](https://developer.apple.com/forums/thread/780682): private data in the system log
- [Detecting Button Presses and Gestures](https://developer.apple.com/library/archive/documentation/General/Conceptual/AppleTV_PG/DetectingButtonPressesandGestures.html): presses go through the responder chain of the focused app

Not found: any Apple document or reverse-engineering report that describes Siri Remote behaviour during a mirroring session.
