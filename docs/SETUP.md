# Set up the Apple TV Agent Bridge

Install one Home Assistant app and one custom integration for all your TVs. The app talks to the TVs. Agents connect to the integration on your Home Assistant web address.

## What you need

- **Apple TV 4K on tvOS 27 or later (recommended).** The tested setup includes an Apple TV 4K (2nd generation) on tvOS 27.2 (24K122). tvOS 26.6 also works but is less tested; an Apple TV 4K (1st generation), which cannot run tvOS 27, paired and worked on 26.6.1. No Apple Developer account is required.
- **Home Assistant OS with Supervisor**, an administrator login, and an aarch64 or amd64 host. Testing used a Home Assistant Green. The integration test environment pins Home Assistant 2026.11.2. The host needs internet access and free disk for the image build.
- **LAN access between HA and each TV.** Allow Bonjour discovery and direct connections. A static IP or DHCP reservation is optional. The bridge tries configured and remembered addresses, then searches Bonjour for the receiver’s identity.
- **A workstation checkout of this repository**, a shell and `rsync`. The automated deploy also needs `uv` and OpenSSH. Run workstation commands from the repository root.

To get a checkout with Git:

```sh
git clone https://github.com/neekolas/ha-apple-tv.git
cd ha-apple-tv
```

## Prepare each TV

1. Wake the Apple TV. In **Settings → AirPlay and Apple Home**, turn on AirPlay. Set **Allow Access** to **Anyone on the Same Network** and leave the AirPlay password off. If Restrictions are enabled, allow AirPlay settings and Remote App pairing.
2. To wake the display with the Apple TV, enable **Settings → Remotes and Devices → Control TVs and Receivers** and HDMI-CEC on the display.
3. If Home Assistant already controls this TV through the built-in **Apple TV** integration, open **Settings → Devices & services → Apple TV** and disable that TV’s entry before pairing the bridge. Keep the entry for rollback. Dashboards and automations using its entities will need the bridge’s entities.

## Install the app and integration

Choose one route. Both install the same two components.

### Automated workstation deploy

Follow the [workstation deploy guide](deploy.md) through the Core restart, then continue at **Pair the first TV** below. The deployer stages the pyatv fork, copies both source trees and waits for the app watchdog and Core.

### Manual install with Samba

1. Open **Settings → Apps → App store** in Home Assistant. Install **Samba share**. Configure a username and password, enable the `local_apps` and `config` shares and start Samba. Mount both shares on your workstation with those credentials. On macOS use Finder’s **Connect to Server** and enter `smb://homeassistant.local`, substituting your HA host. Older installs may label the local apps share `addons`. [Samba share documentation](https://github.com/home-assistant/addons/blob/master/samba/DOCS.md)
2. From the repository root, stage the fork and copy both trees. Set the variables to your actual mount points. These examples use macOS mount paths.

   ```sh
   LOCAL_APPS='/Volumes/local_apps'
   HA_CONFIG='/Volumes/config'
   app/build/stage-context.sh
   ```

   After staging succeeds, confirm both shares are mounted at those paths. Then run:

   ```sh
   mkdir -p "$LOCAL_APPS/atv_bridge" "$HA_CONFIG/custom_components/atv_bridge"
   rsync -rltp --exclude=.git --exclude=.venv --exclude=__pycache__ \
     --exclude=.pytest_cache --exclude='*.egg-info' app/ "$LOCAL_APPS/atv_bridge/"
   rsync -rltp --exclude=__pycache__ custom_components/atv_bridge/ \
     "$HA_CONFIG/custom_components/atv_bridge/"
   ```

   Check for `atv_bridge/config.yaml` and `atv_bridge/vendor/pyatv/pyproject.toml` on the local apps share, and `custom_components/atv_bridge/manifest.json` on the config share. Keep the build scripts executable. The fork supplies the tvOS pairing and URL playback fixes.
3. In **Settings → Apps → App store**, use the menu to **Check for updates**. Under **Local apps**, open **Apple TV Agent Bridge** and select **Install**. On **Configuration**, leave the TV address empty for discovery. Save any changes and select **Start**. [Home Assistant local app instructions](https://contributors.home-assistant.io/apps/first-app/)
4. Open **Settings → System**, select the power menu and **Restart Home Assistant**. Wait for Core to return. In **Settings → Devices & services**, look for the discovered **Apple TV Agent Bridge**. If it is missing, restart the bridge app to publish discovery again.

## Pair the first TV

Keep the intended TV awake and in view. Each PIN exchange expires after 180 seconds.

1. In **Settings → Devices & services**, configure the discovered **Apple TV Agent Bridge**. Submit **Pair the discovered bridge**. If **Choose an Apple TV** appears, select the TV by its name and address.
2. Enter the four-digit **AirPlay Code** from that TV. The controller name is **Agent Bridge**. The app tests whether it can reuse the AirPlay credentials for Companion control and RAOP audio. If HA offers **Optional Companion pairing**, leave **Pair this protocol** checked and enter the additional PIN shown on the TV.
3. For accessibility text, labeled navigation and screenshots, complete **Optional screenshot pairing**. First open **Settings → Remotes and Devices → Remote App and Devices** on the TV and leave it open. Then submit the checked option in HA and enter the six-digit PIN shown on that TV. To finish initial setup with control only, clear the checkbox. You can add screen pairing later.
4. If the PIN fails or expires, use **Request a new PIN** and enter the newly displayed digits. On completion, the bridge provides a media player, remote, **Health**, **Connected**, **Keyboard focused**, and **Run self-test** for the receiver.

### Add another TV or repair a pairing

- To add another TV, choose **Add Apple TV** on the existing integration entry. Select the receiver from **Choose an Apple TV**. The list shows discovered receivers and registered TVs that are not fully paired. A TV with both control and screen pairing is not offered, and the flow stops with "already configured" if you reach it. For a new receiver, enter its four-digit AirPlay PIN. For a registered TV that already has control pairing, the flow skips the AirPlay step and continues with the remaining pairings.
- Each remaining pairing opens a preparation screen. For screen pairing, open **Settings → Remotes and Devices → Remote App and Devices** on the TV, then submit and enter the six-digit PIN. To add the TV with control only, select **Skip screen pairing** on that screen. The TV is added without screen pairing. Add it later from **Configure → Re-pair screen**, the app's **Apple TV Bridge** panel, or `tv_pair` with `"kind":"screen"`.
- For a missing or invalid pairing, open **Configure**, select the affected TV and choose **Re-pair control** or **Re-pair screen**. Replacement clears that pairing’s local credentials when it starts. For screen repair, open the TV’s pairing menu first.
- The app’s **Apple TV Bridge** web UI also offers **Pair control** and **Pair screen** for registered receivers. A connected agent can use `tv_pair`. Start with `{"tv":"<TV name>","kind":"screen"}` or use `"control"` for AirPlay. Relay `data.ask_user`. Poll with the same `tv`, `kind` and returned `pairing_id` while the state is `waiting_for_pairing_screen`. At `code_on_screen`, submit those fields plus `code` as a string. Preserve leading zeros. To cancel, send those fields with `"cancel":true` in place of the code.
- For PIN prompts on a phone, choose **Configure → Pairing notifications** and select its Home Assistant Companion app notify service. An agent can request a notification with `"notify":true` when starting `tv_pair`.

## App options

Set these in **Settings → Apps → Apple TV Agent Bridge → Configuration**. Save and restart the app to load changed options.

| Option | Default | Accepted values and behavior |
| --- | --- | --- |
| `apple_tvs` | `[]` | List of receivers. For each, supply at least one of `name`, `host` (IPv4) or `id` (advertised identifier). For discovery by name alone, use the TV’s exact advertised name. An empty list lets you select TVs during setup. |
| `apple_tv_host` | empty | Optional preferred IPv4 address for one receiver. The resolver can fall back to discovery after its address changes. |
| `apple_tv_id` | empty | Optional advertised identifier for that receiver. These two single-TV options can coexist with `apple_tvs`. |
| `max_active_mirrors` | `1` | Positive integer. Maximum concurrent mirror sessions across the TVs. |
| `presentation_mode` | `mirror` | `mirror` streams the Chromium tab. `play_url` renders supplied HTML, files or bundles to MP4. `auto` can fall back to MP4 after two failed mirror starts. Live URLs always mirror. |
| `mirror_size` | `1920x1080` | `1920x1080` or `1280x720`. Use the smaller size to reduce encoding work. |
| `page_scripts` | `true` | Permit HA admin callers to run JavaScript in the presented document. Set to `false` and restart the app to disable `tv_page_run`. |
| `mirror_fps` | `10` | Integer from 1 through 60. Higher frame rates need more CPU. |
| `default_profile` | empty | Preferred label on the tvOS profile picker. The bridge needs screen pairing to read and select it. |
| `app_profile` | empty | Preferred label on an in-app profile picker. If empty, the bridge tries the selected tvOS profile or `default_profile`. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |

## Connect an agent

1. Sign in to Home Assistant as an administrator. Open your profile → **Security → Long-lived access tokens → Create token**. Copy the token when it appears. Create a separate token for each agent so you can revoke its access independently. [Home Assistant authentication](https://contributors.home-assistant.io/core/internals/auth-api/)
2. Set `HA_URL` to the scheme, host and port you use for HA, without a trailing slash. The standard local origin is `http://homeassistant.local:8123`. Use HTTPS or a trusted network when sending an admin token. Set `HA_TOKEN` from your secret store or paste it at a hidden prompt. For Bash:

   ```bash
   HA_URL='http://homeassistant.local:8123'
   read -r -s -p 'Home Assistant token: ' HA_TOKEN
   printf '\n'
   ```

3. Register `$HA_URL/api/atv_bridge/mcp` in your client using **Streamable HTTP** and the header `Authorization: Bearer <token>`. For Claude Code, run this in the same shell. [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp)

   ```sh
   claude mcp add --transport http apple-tv "$HA_URL/api/atv_bridge/mcp" \
     --header "Authorization: Bearer $HA_TOKEN"
   ```

4. For Home Assistant Assist, expose each intended bridge media player under **Settings → Voice assistants → Expose**. The integration supplies `atv_bridge__show`, `atv_bridge__play`, `atv_bridge__open`, `atv_bridge__power`, `atv_bridge__pair` and `atv_bridge__status`. The administrative Home Assistant LLM API gets these tools when the entry is loaded.

### Verify without changing the screen

Call `tv_status {}` in MCP, or use the same shell variables for REST:

```sh
curl -fsS -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -d '{}' "$HA_URL/api/atv_bridge/tool/tv_status"
```

- With one paired TV, inspect `data.tv.reachable`, `data.pairings`, `data.capabilities` and `data.problems`. For full operation on a reachable TV, control and screen pairing should both be `"ok"`, with `show`, `play_media`, `look_text` and `look_image` true. The top-level `ok` means the check ran.
- With several paired TVs, the same call returns `data.tvs`. Check each row’s `ok`, `status.tv.reachable`, `status.pairings` and `status.problems`. For the full single-TV response, call `tv_status {"tv":"<TV name>"}`. Use the bridge ID from `tv://tvs` if two names match.
- On an awake, screen-paired TV, call `tv_look {"tv":"<TV name>"}`. Look for `data.items` and `screen.focus`. Focus can be null. A text read does not press remote keys or take a screenshot.

### Show, update and dismiss a page

Choose a TV that is free to use. Replace `<TV name>` in all three MCP calls with its name or bridge ID. Perform them in order.

1. Show a page with a function that changes its heading.

   ```json
   {"tv":"<TV name>","html":"<h1 id='step'>Step 1</h1><script>window.setStep = n => document.getElementById('step').textContent = 'Step ' + n</script>"}
   ```

   Pass this to `tv_show`. Read `verified` and `evidence` to see what the bridge confirmed.
2. Call `tv_page_run {"tv":"<TV name>","script":"window.setStep(arg)","arg":2}`. The page should display “Step 2”. Check that `data.result` is `"Step 2"`.
3. Call `tv_show {"tv":"<TV name>"}` to stop that presentation. With one paired TV, `tv_show {}` also dismisses it.

For multiple files, supply `files` with `index.html` at its root. Use relative asset paths and a `base64:` prefix for binary file values. For a ZIP, put `index.html` at the archive root and upload the raw archive:

```sh
curl -fsS -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/zip' \
  --data-binary @page.zip "$HA_URL/api/atv_bridge/bundle"
```

Pass the returned string to `tv_show {"tv":"<TV name>","bundle":"<returned bundle>"}`. For app control, read `tv://apps/<TV ID>`, then use `tv_open`, `tv_look` and `tv_navigate`. [Tool examples](../README.md#operate-a-tv)

## Tailscale for remote agents and live pages

Tailscale is optional. A live page must be reachable **from the HA host**, where Chromium runs. If the agent and its web server already have LAN access, pass the server’s LAN URL to `tv_show`.

1. Install the **Tailscale** community app on HA and sign it in to the same tailnet as the agent. Set `userspace_networking: false` for outbound access from HA. Enable MagicDNS and HTTPS certificates in the Tailscale admin console if you want to share a local web app with HTTPS. [HA Tailscale app](https://github.com/hassio-addons/app-tailscale/blob/main/tailscale/DOCS.md)
2. Allow the agent to reach the HA web port in your tailnet policy. Set the agent’s `HA_URL` to HA’s tailnet hostname and web port. To show an agent-hosted page, also allow HA to reach the serving port on the agent. If you prefer HA’s LAN address, advertise the LAN subnet with the HA Tailscale app’s `advertise_routes` option and approve that route in the Tailscale admin console. On a Linux agent, accept subnet routes with `tailscale set --accept-routes`. Permit the HA LAN address and web port in the tailnet policy. [Tailscale subnet routers](https://tailscale.com/kb/1019/subnets)
3. If the bridge cannot resolve tailnet names, record the current upstream servers with `ha dns info` in Terminal & SSH. To use Tailscale DNS, run `ha dns options --servers dns://100.100.100.100`. This replaces the Supervisor DNS server list. `tv_status` reports `tailnet_dns_missing` if its tailnet DNS check fails. LAN-only installations can ignore this problem. [HA Tailscale DNS setup](https://github.com/hassio-addons/app-tailscale/blob/main/tailscale/DOCS.md#dns)
4. With a web server running on the agent’s loopback port 3000, run:

   ```sh
   tailscale serve --bg --https=8443 http://127.0.0.1:3000
   ```

   Pass the HTTPS URL printed by Tailscale to `tv_show` with the intended `tv`. Keep the web server running while the page is shown. Use `tailscale serve --bg --https=8443 off` to remove that share. [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Bridge discovery missing | Confirm the integration’s `manifest.json` is under `custom_components/atv_bridge`. Restart Core, start the app, then restart the app to republish discovery. |
| No TV in the pairing list or no PIN | Wake the intended TV. Check LAN discovery, AirPlay access and any configured IPv4 address. For a screen PIN, leave **Remote App and Devices** open before submitting. Request a new PIN after expiry. |
| Agent receives HTTP 401, 403, 502 or 503 | Check the HA origin, bearer token and admin role. Check the loaded integration entry and app state. The proxy returns 503 when there is no loaded entry and 502 when it cannot connect to the app. |
| `tv_required` or `tv_not_found` | Read `tv://tvs` and pass one listed bridge ID. Names must match one TV. |
| `access_blocked`, `not_paired` or `pairing_removed` | Check **Allow Access** on that TV. Check control pairing in `tv_status`. Use **Configure → Re-pair control** for rejected credentials. Complete fallback Companion pairing if offered. |
| `tv_unreachable` | Wake the TV with the Siri Remote. Check LAN access and any configured address. Use **Run self-test** on that receiver’s HA device. |
| `screen_unavailable` | Check screen pairing in `tv_status`. For `screen_pairing_invalid`, open the TV pairing menu and choose **Configure → Re-pair screen**. An app with no accessible labels can also cause this error. |
| `mirror_busy` or `busy` | For `mirror_busy`, inspect `data.holders`. Dismiss the intended holder, or set `replace: true` on the new show if stopping another TV’s presentation is intended. For `busy`, let the current operation finish. |
| `page_unreachable` | Check the URL from the HA network, DNS, TLS, server and app logs. The same code can report a missing bundle or sender failure. Use the returned `error.fix`. |
| `uncertain` or `verified: false` after input | Read the evidence and use a targeted `tv_look` before sending more input. |
| Page stutters | Try a lower `mirror_fps` or `mirror_size: 1280x720`. |

Find app logs under **Settings → Apps → Apple TV Agent Bridge → Log** and integration logs under **Settings → System → Logs**. The LAN watchdog at `http://<HA host>:8766/api/health` should return `"ok":true` and `"state":"running"` even before pairing. It checks the app process. Use targeted `tv_status` to check the receiver.

## Updates and removal

- **Update.** Keep the installed app and its data. Update the checkout and use `push-app` and `push-integration`, or stage the fork and replace both source directories on the shares. Refresh the app store, choose **Update** for a changed version or **Rebuild** for the same version, and restart Core for integration changes. Per-TV credentials live in `/data/tvs/<bridge ID>/` inside the app. Migration from the old single-TV format preserves a copy under `/data/single-tv-backup/`.
- **After a tvOS update.** Run a targeted `tv_status`. If control or screen pairing needs repair, choose that TV in **Configure**.
- **Remove.** Delete the bridge entry in **Devices & services**. Remove `custom_components/atv_bridge` from the HA configuration and restart Core. Uninstall the bridge app. Remove its `atv_bridge` source folder from the local apps share. Remove the bridge’s saved pairings on the TVs and re-enable any built-in entries you disabled. Revoke the bridge tokens in the administrator’s profile.
- **Optional network cleanup.** Turn off any Tailscale Serve share you created. If you changed Supervisor DNS solely for the bridge, restore the saved server list. If it was empty, run `ha dns reset` followed by `ha dns restart`. [HA Tailscale DNS setup](https://github.com/hassio-addons/app-tailscale/blob/main/tailscale/DOCS.md#dns)

The bridge has no purchase guard. Configure your agent to ask before purchases if you want confirmation. Port 8766 serves uploaded page content and rendered media to the LAN without authentication. Keep private content and this port within your trusted network.
