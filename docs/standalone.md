# Run tv-use without Home Assistant

Standalone mode runs the tv-use app image as a plain MCP server in a Docker container. The container talks to the Apple TVs directly. It needs no Home Assistant. It is the same image that the Home Assistant app uses, `ghcr.io/neekolas/tv-use-bridge`.

## When to choose it

Choose standalone mode when you do not run Home Assistant, or when you want a faster computer to render pages. Chromium renders every page, and the bridge encodes it as video, on the computer that runs the bridge. A Mac does this much faster than a Home Assistant Green. [Measured results](#measured-results)

The trade-off: the computer that runs the container must stay on and awake. When the Mac sleeps, the agent loses the TV.

| | Home Assistant app | Standalone container |
| --- | --- | --- |
| Agent endpoint | `<HA origin>/api/atv_bridge/mcp` with an HA admin token | `http://<host>:8765/mcp` with a token that you choose |
| Configuration | App options in Home Assistant | Environment variables or command flags |
| Pairing | HA setup flow, the pairing panel, phone notifications or `tv_pair` | `tv_pair` only |
| HA entities and Assist tools | Yes | No |
| Discovery without a TV address | Yes | Not on a Mac. Give each TV's IP address. |
| `presentation_mode` `play_url` and `auto` | Yes | Not on a Mac |

## What you need

- An Apple TV prepared as in [Prepare each TV](SETUP.md#prepare-each-tv). tvOS 27 or later is recommended.
- A Mac with [OrbStack](https://orbstack.dev), or a Linux computer with Docker. Testing used OrbStack 2.2.5 on Apple silicon (`linux/arm64`). Docker Desktop is untested.
- The IP address of each TV. Give each TV a DHCP reservation, because the container cannot find a TV whose address has changed. To look up an address from the Mac, run `dns-sd -G v4 Living-Room.local` with the TV's name, or open **Settings → Network** on the TV.

## Start the container

1. Create a token. Clients send it as `Authorization: Bearer <token>`. The container refuses to start without a token of at least 16 characters.

   ```sh
   mkdir -p "$HOME/.config/tv-use"
   (umask 077 && openssl rand -hex 32 > "$HOME/.config/tv-use/token")
   ```

2. Create a volume for pairing records and other state. The container keeps everything in `/data`.

   ```sh
   docker volume create tv-use-data
   ```

3. Start the container. Replace the TV name and address with your own.

   ```sh
   docker run -d --name tv-use --restart unless-stopped --network host \
     -v tv-use-data:/data \
     -v "$HOME/.config/tv-use/token:/run/secrets/tv-use-token:ro" \
     -e TV_USE_TOKEN_FILE=/run/secrets/tv-use-token \
     -e 'TV_USE_TVS=name=Living Room,host=192.168.1.20' \
     ghcr.io/neekolas/tv-use-bridge:latest
   ```

   With `--network host`, the container uses the network of the OrbStack virtual machine, and OrbStack forwards its ports to the Mac. See [Networking](#networking). After you pair the TV, add its ID to `TV_USE_TVS`. See [Keep each TV's ID](#keep-each-tvs-id).

4. Check the container. The first command needs no token. It reports only that the app runs. The second reports the TV and its pairings. `GET /api/health` on port 8765, with the token, reports the TV connection and pairings in short form.

   ```sh
   curl -sS http://127.0.0.1:8766/api/health
   curl -sS -H "Authorization: Bearer $(cat "$HOME/.config/tv-use/token")" \
     -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8765/api/tool/tv_status
   ```

   The log line `tv-use standalone: MCP at http://0.0.0.0:8765/mcp with a bearer token` shows that standalone mode started. Read it with `docker logs tv-use`.

## Settings

Set each setting with an environment variable (`-e` in `docker run`) or a command flag. A flag wins over its variable. To pass flags, give the full command after the image name:

```sh
docker run ... ghcr.io/neekolas/tv-use-bridge:latest \
  python -m atv_bridge_app --standalone --tv 'name=Living Room,host=192.168.1.20' --mirror-fps 30
```

`python -m atv_bridge_app --help` lists every flag.

| Variable | Flag | Default | Meaning |
| --- | --- | --- | --- |
| `TV_USE_TOKEN` | `--token` | none | Bearer token that clients must send. At least 16 characters. A variable or flag value is visible in `docker inspect`. Prefer a token file. |
| `TV_USE_TOKEN_FILE` | `--token-file` | none | File that holds the token. Use this or `TV_USE_TOKEN`, not both. |
| `TV_USE_INSECURE_LOCAL` | `--insecure-local` | off | Accept requests without a token. The server then listens on loopback only and accepts only the host names `localhost`, `127.0.0.1` and `::1`. Any program on the computer can then control the TV. Use it only for a short test on a computer you trust. |
| `TV_USE_TVS` | `--tv` | none | The TVs. Write each TV as `host=IP,id=ID,name=NAME` with any of the three keys, or as a bare IPv4 address. Separate TVs with semicolons in the variable. Repeat the flag for more TVs. The ID is the TV's advertised identifier from `tv://tvs`. [Keep the ID in the list.](#keep-each-tvs-id) |
| `TV_USE_LISTEN` | `--listen` | `0.0.0.0` | Address for MCP and REST. `127.0.0.1` with `--insecure-local`. |
| `TV_USE_PORT` | `--port` | `8765` | Port for MCP and REST. It cannot be 8766. |
| `TV_USE_STAGE_LISTEN` | `--stage-listen` | `127.0.0.1` | Address of the stage on port 8766. See [Networking](#networking). |
| `TV_USE_PRESENTATION_MODE` | `--presentation-mode` | `mirror` | `mirror`, `play_url` or `auto`. |
| `TV_USE_MIRROR_SIZE` | `--mirror-size` | `1920x1080` | `1920x1080` or `1280x720`. |
| `TV_USE_MIRROR_FPS` | `--mirror-fps` | `10` | 1 through 60. |
| `TV_USE_MAX_ACTIVE_MIRRORS` | `--max-active-mirrors` | `1` | Concurrent mirror sessions across the TVs. |
| `TV_USE_PAGE_SCRIPTS` | `--page-scripts`, `--no-page-scripts` | `true` | Allow `tv_page_run`. |
| `TV_USE_PAGE_NETWORK_ALLOW` | `--page-network-allow` | none | Extra destinations for pages: hostnames, IPs, CIDRs or `host:port`. Separate entries with commas in the variable. Repeat the flag for more. |
| `TV_USE_DEFAULT_PROFILE` | `--default-profile` | none | Preferred tvOS profile label. |
| `TV_USE_APP_PROFILE` | `--app-profile` | none | Preferred in-app profile label. |
| `TV_USE_WATCH_HISTORY` | `--watch-history`, `--no-watch-history` | `true` | Record what each TV plays for `tv_history`. |
| `TV_USE_HISTORY_INTERVAL` | `--history-interval` | `300` | Seconds between samples while nothing changes, 30 through 3600. |
| `TV_USE_HISTORY_FAST_INTERVAL` | `--history-fast-interval` | `15` | Seconds to the next sample after a change, 5 through 300. |
| `TV_USE_HISTORY_MAX_ENTRIES` | `--history-max-entries` | `2000` | Entries kept per TV, 100 through 20000. |
| `TV_USE_LOG_LEVEL` | `--log-level` | `info` | `debug`, `info`, `warning` or `error`. |
| `ATV_BRIDGE_DATA` | none | `/data` | State directory inside the container. |

The settings match the [Home Assistant app options](SETUP.md#app-options). A bad value stops the container with a message in `docker logs`.

### Keep each TV's ID

Add each TV's ID to `TV_USE_TVS` after you pair it. The bridge matches an entry to its saved records by the ID, or by the address when the entry has no ID. Without an ID, a changed address counts as a new TV, and the bridge deletes the old TV's pairing records. To find the ID, read `identifiers` from `tv://tvs` or `GET /api/tvs` after pairing. Add `id=<first identifier>` to the entry, keep the same address and restart. A later address change then needs only a new `host=`.

A TV that leaves the list loses its pairing records at the next start. An empty `TV_USE_TVS` removes every TV that the list named before.

### How the container chooses its mode

The container uses Home Assistant mode when the Supervisor gives it a token or when `/data/options.json` exists. Otherwise it uses standalone mode. To force a mode, set `TV_USE_MODE=standalone` or `TV_USE_MODE=ha`, or pass `--standalone`. Home Assistant mode accepts no command flags.

## Pair a TV

Pair through the `tv_pair` tool. Standalone mode has no pairing panel, because a browser page cannot send the bearer token. Each pairing needs a person who can read a code on the TV. Each PIN exchange expires after 180 seconds.

The TV lists the container as **Agent Bridge** under **Remote App and Devices**. The container has its own identity in the volume. A TV that is also paired with the Home Assistant app lists two **Agent Bridge** entries.

1. **Control.** Call `tv_pair {"tv":"Living Room","kind":"control"}`. The TV shows a four-digit AirPlay code. Call `tv_pair` again with the same `tv` and `kind`, the returned `data.pairing_id` and `"code":"1234"`, using the digits on the TV as a string. The bridge then tests whether the AirPlay credentials also work for Companion, the remote control protocol.
2. **Companion, if needed.** If `tv_status` still reports a control problem after step 1, pair Companion through REST. Post `{"protocol":"companion"}` to `/api/tvs/<TV ID>/pair/start`. Then post `{"pairing_id":"<returned ID>","pin":"<code on the TV>"}` to `/api/tvs/<TV ID>/pair/finish`.
3. **Screen.** Call `tv_pair {"tv":"Living Room","kind":"screen"}`. If the state is `waiting_for_pairing_screen`, open **Settings → Remotes and Devices → Remote App and Devices** on the TV and leave it open. Poll with the same `tv`, `kind` and `pairing_id` until the state is `code_on_screen`. Then submit the six-digit code as a string.
4. Call `tv_status {"tv":"Living Room"}`. Control and screen pairing should both be `"ok"`.

Submit the digits once. If a code fails, start a new exchange and wait for a new code. Many failed attempts can make the TV refuse pairing until it restarts.

Pairing records live in the volume under `/data/tvs/<TV ID>/`. Keep the volume to keep the pairings. `docker volume rm` deletes them.

## Connect an agent

Use the Streamable HTTP endpoint `http://127.0.0.1:8765/mcp` and the header `Authorization: Bearer <token>`. For Claude Code, run:

```sh
claude mcp add --transport http tv-use http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $(cat "$HOME/.config/tv-use/token")"
```

For Codex, export the token in the shell that starts Codex, then run:

```sh
export TV_USE_TOKEN="$(cat "$HOME/.config/tv-use/token")"
codex mcp add tv-use --url http://127.0.0.1:8765/mcp --bearer-token-env-var TV_USE_TOKEN
```

An agent on another computer on the LAN uses `http://<Mac IP address>:8765/mcp`. Use the same token. Send it only over a trusted network, because plain HTTP carries the token in clear text.

Scripts can POST tool arguments to `/api/tool/<name>`. Upload a ZIP to `/api/bundle`. These are the same routes that the Home Assistant integration uses, under `/api/` instead of `/api/atv_bridge/`. The tools and results are the same as in [the MCP API](mcp-api.md).

## Networking

### On a Mac with OrbStack

OrbStack runs containers in a Linux virtual machine. With `--network host`, the container shares the network of that machine, not the Mac's. OrbStack forwards each TCP and UDP port that the container listens on to the Mac:

- A port on `0.0.0.0` in the container opens on every Mac interface. Other devices on the LAN can reach it. This needs the OrbStack setting **Expose ports to LAN** (`docker.expose_ports_to_lan`), which is on by default.
- A port on `127.0.0.1` in the container opens only on the Mac's loopback interface.
- Connections from the container to the LAN leave through the Mac's IP address.

Control, screen reading, screenshots and AirPlay mirroring with sound all work this way.

What does not cross the virtual machine:

- **Multicast.** Bonjour discovery finds no TVs. Give each TV's IP address in `TV_USE_TVS`. Queries sent straight to a known address work.
- **The Mac's address.** The container sees only its virtual address. `presentation_mode` `play_url` and `auto` send the TV a link to an MP4 file on that address, and the TV cannot reach it. Use `mirror`.

### Without host networking

The container also works on Docker's default bridge network. In testing on OrbStack, control, screen reading and mirroring with sound worked. The mirror ran for about 35 seconds. Longer sessions were tested only with `--network host`. Publish only the MCP port. For example, to accept agents on this Mac only:

```sh
docker run -d --name tv-use --restart unless-stopped -p 127.0.0.1:8765:8765 \
  -v tv-use-data:/data \
  -v "$HOME/.config/tv-use/token:/run/secrets/tv-use-token:ro" \
  -e TV_USE_TOKEN_FILE=/run/secrets/tv-use-token \
  -e 'TV_USE_TVS=name=Living Room,host=192.168.1.20' \
  ghcr.io/neekolas/tv-use-bridge:latest
```

Use `http://127.0.0.1:8765/mcp`. The server listens on IPv4 only. With `-p 8765:8765`, which also accepts agents from the LAN, Docker opens the port on IPv6 too and resets those connections, so `localhost` can fail. Port 8766 stays inside the container, so check the container with `GET /api/health` on port 8765.

### On Linux

Linux was not tested. There, `--network host` gives the container the host's own network, as in Home Assistant, so Bonjour discovery should work. For `play_url` or `auto`, also set `TV_USE_STAGE_LISTEN=0.0.0.0`, so that the TV can fetch the MP4 files from port 8766.

### Security

- Port 8765 needs the token for every route.
- Port 8766, the stage, serves the bridge's own browser. It listens on `127.0.0.1` by default. With `--network host` on OrbStack, every client that OrbStack forwards appears to come from `127.0.0.1`, so the stage cannot tell the browser from a LAN device. Keep the default on a Mac.
- Pages cannot reach the container's own addresses or loopback. On a Mac, the container cannot see the Mac's LAN address, so it cannot protect it. Grant exact `host:port` entries in `TV_USE_PAGE_NETWORK_ALLOW`, never a subnet that contains the Mac. Otherwise a page can reach every service on the Mac that listens on the LAN.

## Measured results

Measured on 21 November 2026 against an Apple TV 4K (2nd generation) on tvOS 27.2. The Mac was on Apple silicon with OrbStack 2.2.5, `--network host` and 16 virtual CPUs. The Home Assistant Green ran the app that was deployed that night. Both showed the same simple page at 1920x1080. Each value comes from one to three runs.

| Measure | Home Assistant Green | Mac, standalone |
| --- | --- | --- |
| First `tv_show`, which starts Chromium and tvpipe | 12.4–13.2 s | 3.0 s (4.2 s for the first show after the container starts) |
| `tv_show` of a new page while a page is showing | 2.6–2.8 s | 1.0–1.3 s |
| Dismiss with `tv_show {}` | 3.0–4.9 s | 1.1 s |
| Text `tv_look` | 0.6 s | 0.9 s |
| Page render rate, whole frame changes every frame | 8 fps | 60 fps |
| Frames sent at `mirror_fps` 10, 30 and 60 | 8 fps or less at 10 and 30. 60 was not measured. | 7.5, 24 and 52 fps |
| CPU while mirroring | 3.3 of 4 cores | 0.6 to 1 core |

- "Frames sent" is the change in tvpipe's count of frames pushed to the TV, with page sound muted. With sound, the count was about 125 per second higher, probably because it then includes audio frames. The Green values come from an earlier frame-rate study on 20–21 November 2026. There, the page render rate limited the sent rate.
- A page where only a focus highlight moves sent 6 fps at `mirror_fps` 10 and 12.5 fps at 30. tvpipe appears to send a frame only when the picture changes.
- These counts are frames sent, not frames the TV showed. Screenshots during the 60 fps run showed a clean picture. The frame counter on the TV was less than one second behind the page.
- The Mac is not CPU bound. On a Mac, `TV_USE_MIRROR_FPS=30` is a good choice for pages that change often. The default of 10 matches the Home Assistant app.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The container exits at start | Read `docker logs tv-use`. A missing or short token, a bad value or a flag in Home Assistant mode stops the container with a message. |
| `401` with `unauthorized` | Send `Authorization: Bearer <token>` with the token from `TV_USE_TOKEN` or `TV_USE_TOKEN_FILE`. With `--insecure-local`, connect to `localhost` or `127.0.0.1`. |
| Connection reset on `localhost:8765` | Without host networking, connect to `127.0.0.1`. The server listens on IPv4 only. |
| `405` on `GET /mcp` | Expected. The server answers MCP with `POST` only. |
| `tv_not_found` or no TV in `tv://tvs` | Add the TV to `TV_USE_TVS` with its IP address and restart the container. Discovery does not work on a Mac. |
| `tv_unreachable` | Wake the TV. Check that the address in `TV_USE_TVS` is current. The container does not find a TV that changed its address. |
| `waiting_for_pairing_screen` does not change | Open **Settings → Remotes and Devices → Remote App and Devices** on the TV and leave it open. |
| `sender_failed` on `tv_show` | Check that the TV is awake and that AirPlay **Allow Access** includes this network. Read `docker logs tv-use`. |
| `tailnet_dns_missing` | Ignore it unless pages use tailnet names. Its fix names a Home Assistant command, which does not apply here. |
| The mirror stutters | Lower `TV_USE_MIRROR_FPS`, or set `TV_USE_MIRROR_SIZE=1280x720`. |

## Update and remove

- **Update.** Run `docker pull ghcr.io/neekolas/tv-use-bridge:latest`, then `docker rm -f tv-use` and the same `docker run` command. The volume keeps the pairings.
- **Remove.** Run `docker rm -f tv-use`. To delete the pairings, also run `docker volume rm tv-use-data`. On each TV, remove **Agent Bridge** under **Settings → Remotes and Devices → Remote App and Devices**.
