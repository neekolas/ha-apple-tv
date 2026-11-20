# Live accessibility on Apple TV

The measurements in this report come from earlier hardware runs. They used an Apple TV 4K (2nd generation), tvOS 27.2 (24K122), and the bridge app on a Home Assistant Green. The Mac called the integration’s authenticated MCP endpoint. The Green owned the paired tunnel throughout. Timings below include the MCP client startup and HTTP round trips.

## What the inspector returned

| Read on the Green | Observed on the TV |
| --- | --- |
| `deviceFetchSpecialElement:0` | The first accessibility element. In Settings this was the page heading, including when a device row several places down the list had remote focus. The platform element included a process ID. Later transition tests showed that this could belong to the previous app. |
| `deviceFetchSpecialElement:1` | The last element. Settings returned a preview view or help text. Kinds 2–5 returned nil. |
| `deviceElement:valueForAttribute:` | `Label` and `Traits` worked on the heading. `Focused`, `AXFocused`, `HasKeyboardFocus`, `FocusedUIElement`, and `AXFocusedUIElement` returned nil. |
| `_AXHierarchyElementsAttribute` | The heading returned an `AXAuditNode_v1` with its role and spoken description. On the tested Settings page it had no children. |
| `deviceFetchElementAtNormalizedDeviceCoordinate:` | Read-only hit tests using archived CGPoint values returned nil, including points on the Settings list. |
| `deviceInspectorSupportedEventTypes` | Returned 2. Enabling the inspector after requesting mask 2 changed the mask back to 1, as confirmed by `hostInspectorMonitoredEventTypeChanged:`. With mask 2 alone the bridge still had no focus result after Back and Up. Screenshots confirmed Up moved from Remotes and Devices to AirPlay and Apple Home. |
| `deviceCaptureScreenshot` | Returned PNG bytes, scale 2 and logical bounds 1920 × 1080. A Settings capture contained 15.5 MB of image data. |

## Reading native focus

On this TV, `com.apple.accessibility.axAuditDaemon.remoteAXService` returned device API version 26. The observer opens it over the paired RSD tunnel. The initial build used the first audit element’s PID. The observer hit-tests the display for a process ID, then requests that process’s native AX snapshot. Native attribute 2094 (`kAXXCAttributeHasNativeFocus`) identifies the row or tile that has Siri Remote focus. Attribute 2001 supplies its label and 2003 supplies the frame for navigation. The bridge compares element IDs within one snapshot. Multiple occurrences of the same handle can describe the same focused element. Multiple distinct focused handles yield unknown focus.

The observer keeps the native tree connection open between reads within an app. It releases the subscription before a launch or Home press and keeps the RSD tunnel open. On a TV with that service, ordinary screen reads do not open the legacy inspector. The legacy hierarchy fallback disables inspector visuals and highlighting. On a channel error the observer can reopen the tunnel and retry once within the request timeout. A timeout closes the channels and fails the read. The read-only archive codec is adapted from [ipb's MIT-licensed implementation](https://github.com/ipbtools/ipb/blob/8c227a10710dad479852aa0203de729fed57471b/Sources/ax_archive.py).

## Before and after a text observation

Starting with AirPlay and Apple Home focused, one Up press moved focus to Notifications. The remote result and the next native snapshot both reported Notifications, and only its list item was marked focused. The Green then ran an image read, a text-only `tv_look`, and a second image read with no intervening remote input.

[Before the text read](live-accessibility/settings-before.png) · [After the text read](live-accessibility/settings-after.png)

The 800 × 450 RGB images differed at **0 of 360,000 pixels**, including the full Settings list and the top-right corner. The decoded pixel SHA-256 of both was `5fe2f930a3328360ffc21d6f18b993aa25c5dd1ce90b4cee06ddaf808f240fc4`. The interposed text read took 3.22 s in the initial native-tree build. Only the explicit image requests used DVT.

## Hardware paths

| Starting focus | MCP request | Path and independent observation |
| --- | --- | --- |
| Settings → Notifications | `tv_navigate {"to":"Remotes and Devices","select":true}` | Down, Down, Select in **8.46 s** on the final deployment. The tool returned verified with Clickpad focused. An independent text read confirmed the heading and focused row in 2.78 s. [Submenu screenshot](live-accessibility/remotes-and-devices.png) |
| Remotes and Devices → Clickpad | `tv_remote {"keys":["menu"]}` | Back in **5.57 s** on the final deployment. The remote call returned verified with Remotes and Devices focused on the Settings root. |
| Home → TV | `tv_navigate {"to":"YouTube"}` | Right five times in 21.38 s. The tool verified YouTube, and an independent look marked just that tile focused. The app remained HeadBoard. [Home row screenshot](live-accessibility/youtube-home-row.png) |

In the earlier accessibility deployment (c974810), one Left press on Home reported the neighboring app tile in 3.65 s. Navigating to YouTube returned verified after one Right press in 4.19 s. A final independent text read took 1.58 s and confirmed HeadBoard with only YouTube focused. That run ended on Home. It logged one audit channel open and one native tree channel open across Settings, Back, Home and tile navigation, with no reconnects.

The native tree contains the accessibility elements the foreground app publishes. A row can remain in that tree while partly or fully outside the viewport. A missing or ambiguous native focus produces null. The fallback for a TV without the native tree service reads the available audit hierarchy and leaves focus unknown. These results cover Settings and Home on tvOS 27.2. The bridge supports tvOS 27 or later. A successful test on tvOS 26.6.1 did not extend that baseline.

## Foreground switches and nested Settings menus

On tvOS 27.2 (24K122), the legacy first-element request returned nil with Settings visible. During a Settings → Home transition it still identified Settings (PID 12154) while a native display hit already identified HeadBoard (PID 10215). The original app also requested `deviceRunningApplications` for foreground verification. The Green logged repeated 25-second timeouts in that call and closed the observer channels on timeout. A paused media session then supplied a stale fallback app name. Explicitly targeting Settings’ running PID in the legacy inspector still returned nil.

On the native channel alone, a center hit at (960, 540) returned PineBoard (the system shell) with one label and no focused row. The DVT screenshot showed the Settings list on the right, with AirPlay and Apple Home highlighted. A hit at (1440, 540) returned TVSettings. Its snapshot contained 14 labels and marked AirPlay and Apple Home as focused. Both coordinates are logical points on the 1920 × 1080 canvas.

Screen reads now request the owner of a displayed element, starting in the right-hand menu area. If its tree has no native focus, the observer samples other regions and skips process IDs it has already read. App, focus, and items come from the same snapshot. The bridge uses the names Home and Settings for HeadBoard and TVSettings. It keeps paused media in `screen.playing`. Each menu selection records the destination focus and checks it against the new item list before the next step.

In an intermediate build, the first two full regressions passed. On the third launch the display hit timed out, followed by timeouts on the native service handshake. A DVT screenshot showed Remotes and Devices focused on the Settings root. The final build releases the old app’s AX subscription before the foreground switch and holds other observer reads until the launch or Home transition has settled. It then opens a fresh native channel on the existing tunnel.

### Reproduce on an idle receiver

The regression sends Home, opens Settings, selects menu entries and sends Back. Use an idle TV with working control and screen pairings and English menu labels. Start with Settings last left at its root. For a read-only check, call `tv_look {"tv":"<room>"}` instead.

The regression needs `python3`, `uv` and a client script that takes a tool name and JSON arguments and prints **one complete JSON tool result**. The default client at `/tmp/atvlab/mcpcall.py` is local to the original lab. Use `--client` on another machine. The runner omits `tv`, so the client must add the intended selector when several TVs are paired.

This adapter uses HA's admin REST proxy for the same tools. Save it as `atv_regression_client.py` on the workstation.

```python
import json
import os
import sys
from urllib.request import Request, urlopen

tool, raw_args = sys.argv[1:]
args = json.loads(raw_args)
args["tv"] = os.environ["TV_NAME"]
request = Request(
    os.environ["HA_URL"].rstrip("/") + "/api/atv_bridge/tool/" + tool,
    data=json.dumps(args).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["HA_TOKEN"],
        "Content-Type": "application/json",
    },
)
with urlopen(request, timeout=120) as reply:
    print(json.dumps(json.load(reply)))
```

Set `HA_URL` to the HA origin, `HA_TOKEN` to an admin token, and `TV_NAME` to the name or ID of the test TV. From the repository root, run the command below. Replace the client path with the saved file. The runner checks one marked focus, both destination headings and both Back transitions. With `--leave-on-devices`, it reopens Remote App and Devices after the last run. [Regression source](live-accessibility/regress.py)

```sh
python3 docs/live-accessibility/regress.py --client /path/to/atv_regression_client.py \
  --runs 3 --leave-on-devices
```

Three consecutive runs passed through the deployed HA MCP endpoint on November 19, 2026 (Pacific time). The whole-run timings include client startup, the Home check, the two-level path, independent text observations and both Back presses.

| Run | Settings path | Look on Remote App and Devices | Whole run |
| --- | ---: | ---: | ---: |
| 1 | 30.30 s | 2.21 s | 55.53 s |
| 2 | 29.59 s | 2.18 s | 58.15 s |
| 3 | 28.51 s | 2.17 s | 56.96 s |

All three started the Settings path with Remotes and Devices already focused. The observed key path was Select, six Down presses, then Select. Each selection produced a focused item in Settings and a tree containing the requested heading. A separate text read matched the final selection’s focused label. After the two Back presses, each run observed Remotes and Devices focused on the Settings root.

The script then reopened the path in 29.25 s and confirmed the final Remote App and Devices heading and focused row with a 2.32 s text read. The Green logged seven native channel opens across the initial observation and foreground switches, with zero observer request failures. The TV was left on this screen.
