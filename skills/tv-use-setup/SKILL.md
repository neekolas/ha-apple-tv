---
name: tv-use-setup
description: Install tv-use end to end, on Home Assistant OS or as a standalone Docker container. Deploy the Apple TV Agent Bridge, pair each Apple TV, create the agent token and verify. Use to install, update, pair, repair or remove the bridge.
---

# Set up tv-use on Home Assistant

Two parts, both named **Apple TV Agent Bridge** in HA: an app that talks to the TVs and an integration that serves agents. You deploy from a workstation checkout. The user handles what needs their eyes or login: TV PIN codes and the HA token.

## Requirements

- Apple TV 4K on tvOS 27 or later (26.6 works, less tested). No Apple Developer account.
- Home Assistant OS with Supervisor, aarch64 or amd64, and an admin login.
- TV and HA on one network with Bonjour allowed.
- Workstation with Git, `uv`, `rsync` and OpenSSH.

## Install

1. `git clone https://github.com/neekolas/tv-use.git && cd tv-use`. Run everything from the repository root.
2. On each TV, **Settings → AirPlay and Apple Home**: AirPlay on, **Allow Access → Anyone on the Same Network**, no password.
3. `export HA_URL='http://homeassistant.local:8123'`, with the user's actual origin and port.
4. Have the user create a token (HA profile → **Security → Long-lived access tokens**) and save it as one line in `~/.config/atv-bridge/ha_token`, mode 600. Never take it in chat.
5. Deploy, in order:

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" check
   uv run tools/deploy/deploy.py --url "$HA_URL" ssh-setup
   uv run tools/deploy/deploy.py --url "$HA_URL" push-app
   uv run tools/deploy/deploy.py --url "$HA_URL" push-integration
   ```

6. If HA's built-in **Apple TV** integration controls a TV you are moving, disable that entry. Keep it for rollback.

## Pair

The user pairs in HA with the TV in view. PINs expire after 180 s.

1. **Settings → Devices & services → Apple TV Agent Bridge → Pair the discovered bridge.** Enter the four-digit AirPlay code. Accept optional Companion pairing.
2. For screen reading, open **Settings → Remotes and Devices → Remote App and Devices** on the TV, then enter the six-digit code in HA.
3. More TVs: **Add Apple TV** on the same entry.

## Verify

Register an agent with **tv-use-connect**, then:

```sh
export HA_TOKEN="$(cat ~/.config/atv-bridge/ha_token)"
curl -fsS -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -d '{}' "$HA_URL/api/atv_bridge/tool/tv_status"
```

Ready when control and screen pairing are `"ok"` and `show`, `play_media`, `look_text` and `look_image` are true.

## Without Home Assistant

The same image runs as a plain MCP server, for example with OrbStack or Docker on a Mac that stays awake. It's much faster than a Green: the first `tv_show` takes about 3 s instead of 12–13 s.

```sh
mkdir -p ~/.config/tv-use && (umask 077; openssl rand -hex 32 > ~/.config/tv-use/token)
docker run -d --name tv-use --restart unless-stopped -p 127.0.0.1:8765:8765 -v tv-use-data:/data \
  -v "$HOME/.config/tv-use/token:/run/secrets/tv-use-token:ro" -e TV_USE_TOKEN_FILE=/run/secrets/tv-use-token \
  -e 'TV_USE_TVS=name=<TV>,host=<IP>' ghcr.io/neekolas/tv-use-bridge:latest
```

Discovery doesn't cross the VM, so give each TV a fixed IP. Pair with `tv_pair`, control first, then screen. `play_url` and `auto` presentation don't work on a Mac. Details: [docs/standalone.md](https://github.com/neekolas/tv-use/blob/main/docs/standalone.md).

## References

| Guide | Load to |
|---|---|
| [docs/SETUP.md](https://github.com/neekolas/tv-use/blob/main/docs/SETUP.md) | Install by hand, repair pairings, app options, Tailscale, troubleshooting, update, remove |
| [docs/deploy.md](https://github.com/neekolas/tv-use/blob/main/docs/deploy.md) | Run, preview or restart individual deploy steps; read logs |
| [app/DOCS.md](https://github.com/neekolas/tv-use/blob/main/app/DOCS.md) | Every app option, presentation and network detail |
| [assist.md](references/assist.md) | Expose the bridge to Home Assistant Assist |

The guides are also in the checkout.
