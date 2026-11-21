---
name: tv-use-setup
description: Set up tv-use end to end. Install the Apple TV Agent Bridge app and integration on Home Assistant OS from a checkout of the repository, pair each Apple TV, create the agent token, connect a first agent and optionally add Tailscale or Assist. Use when someone wants to install, update, pair, repair or remove the bridge.
---

# Set up tv-use on Home Assistant

tv-use has two parts in Home Assistant, both named **Apple TV Agent Bridge**: an app that talks to the TVs and a custom integration that serves agents. Setup runs from a workstation with a checkout of the repository. The user does the steps that need their eyes or their HA login: PIN codes shown on the TV and creating a token.

Related skills: **tv-use-connect** registers an agent and controls the TV. **tv-use-web-apps** and **tv-use-realtime** come after setup.

## Check the requirements

- An Apple TV 4K on **tvOS 27 or later** (recommended and tested). tvOS 26.6 also works but is less tested. No Apple Developer account.
- **Home Assistant OS** with Supervisor on an aarch64 or amd64 host, and an HA administrator login. Testing used a Home Assistant Green.
- The TV and HA on the same network, with Bonjour discovery allowed. A static IP is optional.
- A workstation with Git, `uv`, `rsync` and OpenSSH.

## Install

1. Get a checkout: `git clone https://github.com/neekolas/tv-use.git && cd tv-use`. Run every later command from its root.
2. On each TV: **Settings → AirPlay and Apple Home** → AirPlay on, **Allow Access → Anyone on the Same Network**, no AirPlay password.
3. Set `HA_URL` to the origin the user opens HA with, including the port: `export HA_URL='http://homeassistant.local:8123'`. Some installs use port 80.
4. Ask the user to create a token: HA profile → **Security → Long-lived access tokens → Create token**. Have them save it as one line in `~/.config/atv-bridge/ha_token` with mode 600. Never ask them to paste it into chat.
5. Deploy, in order. Each step checks its result:

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" check
   uv run tools/deploy/deploy.py --url "$HA_URL" ssh-setup
   uv run tools/deploy/deploy.py --url "$HA_URL" push-app
   uv run tools/deploy/deploy.py --url "$HA_URL" push-integration
   ```

   These configure Terminal & SSH, stage the vendored pyatv fork, build and start the app, install the integration and restart HA Core.
6. If HA's built-in **Apple TV** integration controls a TV you are moving, disable that TV's entry under **Settings → Devices & services → Apple TV**. Keep it for rollback.

## Pair each TV

The user does this in HA with the TV in view. Each PIN expires after 180 seconds.

1. **Settings → Devices & services** → the discovered **Apple TV Agent Bridge** → **Pair the discovered bridge**. Choose the TV if asked. Enter the four-digit AirPlay code from the TV. Complete the optional Companion pairing if offered.
2. For screen reading, open **Settings → Remotes and Devices → Remote App and Devices** on the TV first. Then accept screenshot pairing in HA and enter the six-digit code.
3. For another TV, choose **Add Apple TV** on the same entry.

## Connect and verify

Register an agent with the **tv-use-connect** skill. Prefer a local connection at home. Then check the result without changing the screen:

```sh
export HA_TOKEN="$(cat ~/.config/atv-bridge/ha_token)"
curl -fsS -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -d '{}' "$HA_URL/api/atv_bridge/tool/tv_status"
```

For full operation, control and screen pairing are both `"ok"`, and `show`, `play_media`, `look_text` and `look_image` are true.

## Load when needed

The full guides live in the repository. Read them from the checkout, or on GitHub.

| Guide | Load it to |
|---|---|
| [docs/SETUP.md](https://github.com/neekolas/tv-use/blob/main/docs/SETUP.md) | Install by hand with Samba, add or repair pairings, set app options, set up Tailscale, troubleshoot, update or remove |
| [docs/deploy.md](https://github.com/neekolas/tv-use/blob/main/docs/deploy.md) | Run each deploy command, preview changes, restart the app, read logs |
| [app/DOCS.md](https://github.com/neekolas/tv-use/blob/main/app/DOCS.md) | Read every app option and the presentation and network details |
| [assist.md](references/assist.md) | Expose the bridge to Home Assistant Assist and its LLM API |
