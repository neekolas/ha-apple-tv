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

3. Start the container. Replace the TV name and address with your own. Give each TV a name, because the name lets the bridge find the TV's records again after a mistake in its address. See [Keep each TV's pairings](#keep-each-tvs-pairings).

   ```sh
   docker run -d --name tv-use --restart unless-stopped -p 127.0.0.1:8765:8765 \
     -v tv-use-data:/data \
     -v "$HOME/.config/tv-use/token:/run/secrets/tv-use-token:ro" \
     -e TV_USE_TOKEN_FILE=/run/secrets/tv-use-token \
     -e 'TV_USE_TVS=name=Living Room,host=192.168.1.20' \
     ghcr.io/neekolas/tv-use-bridge:latest
   ```

   The container uses Docker's default bridge network. `-p 127.0.0.1:8765:8765` publishes only the MCP port, and only to this Mac. The container's other ports stay on its own loopback, which no program on the Mac can reach. See [Networking](#networking). After you pair the TV, add one of its advertised identifiers to `TV_USE_TVS` as `id=`.

4. Check the container. The first command reports the TV connection and pairings in short form. The second reports the TV and its pairings in full.

   ```sh
   curl -sS -H "Authorization: Bearer $(cat "$HOME/.config/tv-use/token")" http://127.0.0.1:8765/api/health
   curl -sS -H "Authorization: Bearer $(cat "$HOME/.config/tv-use/token")" \
     -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8765/api/tool/tv_status
   ```

   Read the log with `docker logs tv-use`. A line that contains `Starting in standalone mode because` names the mode and the reason. A line that contains `tv-use standalone: MCP at` shows that the server listens. With the command above, the two lines are:

   ```text
   INFO:__main__:Starting in standalone mode because TV_USE_TOKEN_FILE is set and Supervisor supplied no token
   INFO:__main__:tv-use standalone: MCP at http://0.0.0.0:8765/mcp with a bearer token, stage on 127.0.0.1:8766, 1 configured TV(s)
   ```

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
| `TV_USE_INSECURE_LOCAL` | `--insecure-local` | off | Accept requests without a token. The server then listens on the container's loopback only and accepts only the host names `localhost`, `127.0.0.1` and `::1`. `-p` cannot reach the container's loopback, so this works only with `--network host`, where any program on the computer can then control the TV. Use it only for a short test on a computer you trust. |
| `TV_USE_TVS` | `--tv` | none | The TVs. Write each TV as `host=IP,id=IDENTIFIER,name=NAME` with any of the three keys, or as a bare IPv4 address. `IDENTIFIER` is one of the TV's advertised identifiers, not its record ID. A name cannot contain a comma, and in the variable it cannot contain a semicolon. Separate TVs with semicolons in the variable. Repeat the flag for more TVs. A flag replaces the whole list from the variable. The container refuses to start with an empty list unless `TV_USE_DISCOVER` is on. [Keep each TV's pairings.](#keep-each-tvs-pairings) |
| `TV_USE_DISCOVER` | `--discover` | off | Allow an empty `TV_USE_TVS` and add TVs that Bonjour discovery finds. Discovery does not work on a Mac. |
| `TV_USE_LISTEN` | `--listen` | `0.0.0.0` | Address for MCP and REST in the container. `127.0.0.1` with `--insecure-local`. |
| `TV_USE_PORT` | `--port` | `8765` | Port for MCP and REST. It cannot be 8766. |
| `TV_USE_STAGE_LISTEN` | `--stage-listen` | `127.0.0.1` | Address of the stage on port 8766: `127.0.0.1` or `0.0.0.0`. See [Networking](#networking). |
| `TV_USE_PRESENTATION_MODE` | `--presentation-mode` | `mirror` | `mirror`, `play_url` or `auto`. |
| `TV_USE_MIRROR_SIZE` | `--mirror-size` | `1920x1080` | `1920x1080` or `1280x720`. |
| `TV_USE_MIRROR_FPS` | `--mirror-fps` | `10` | 1 through 60. |
| `TV_USE_MIRROR_FPS_MUTED` | `--mirror-fps-muted` | `15` | 0 through 60. A page shown with `audio:"mute"` uses the larger of this and `TV_USE_MIRROR_FPS`. 0 turns it off. |
| `TV_USE_MAX_ACTIVE_MIRRORS` | `--max-active-mirrors` | `1` | Concurrent mirror sessions across the TVs. |
| `TV_USE_PAGE_SCRIPTS` | `--page-scripts`, `--no-page-scripts` | `true` | Allow `tv_page_run`. |
| `TV_USE_PAGE_NETWORK_ALLOW` | `--page-network-allow` | none | Extra destinations for pages: hostnames, IPs, CIDRs or `host:port`. Tailnet addresses need an entry too, such as `100.64.0.0/10`. Separate entries with commas in the variable. Repeat the flag for more. [Security](#security) |
| `TV_USE_HOST_ADDRESSES` | `--host-address` | none | The addresses of the computer that runs the container, as IPs or CIDRs, such as the Mac's LAN and tailnet addresses. Pages never reach them, even through `TV_USE_PAGE_NETWORK_ALLOW`. Separate entries with commas in the variable. Repeat the flag for more. This setting has no Home Assistant app option. |
| `TV_USE_DEFAULT_PROFILE` | `--default-profile` | none | Preferred tvOS profile label. |
| `TV_USE_APP_PROFILE` | `--app-profile` | none | Preferred in-app profile label. |
| `TV_USE_WATCH_HISTORY` | `--watch-history`, `--no-watch-history` | `true` | Record what each TV plays for `tv_history`. |
| `TV_USE_HISTORY_INTERVAL` | `--history-interval` | `300` | Seconds between samples while nothing changes, 30 through 3600. |
| `TV_USE_HISTORY_FAST_INTERVAL` | `--history-fast-interval` | `15` | Seconds to the next sample after a change, 5 through 300. |
| `TV_USE_HISTORY_MAX_ENTRIES` | `--history-max-entries` | `2000` | Entries kept per TV, 100 through 20000. |
| `TV_USE_LOG_LEVEL` | `--log-level` | `info` | `debug`, `info`, `warning` or `error`. |
| `ATV_BRIDGE_DATA` | none | `/data` | State directory inside the container. |

The settings match the [Home Assistant app options](SETUP.md#app-options). A bad value stops the container with a message in `docker logs`. A `TV_USE_` variable that is not a setting, such as a misspelled name, gets a warning in the log and has no effect.

### Keep each TV's pairings

`tv://tvs` and `GET /api/tvs` identify each TV in two ways:

- The **record ID**, in `id`, such as `8fbc2b6530915fde8e449c7e72027484`. The bridge gives this ID to its records of the TV. The log, `DELETE /api/tvs/<record ID>` and the folder `/data/tvs/<record ID>/` use it.
- The **advertised identifiers**, in `identifiers`, such as `2A:74:70:4C:0A:71`. The TV advertises them on the network. `id=` in `TV_USE_TVS` takes one of them. It does not take the record ID.

The bridge never deletes a TV's pairing records because of a change to the TV list. A TV can leave the list through a typo in its address, a misspelled variable, a start with one `--tv` flag, or a second container on the same volume. The bridge then keeps the TV's records in the volume and leaves the TV out of `tv://tvs`. The log names each kept TV and its record ID:

```text
WARNING:atv_bridge_app.fleet:Kept the records of TV Living Room (ID 8fbc2b6530915fde8e449c7e72027484), which the TV list no longer names. Add the TV to the list again to use its pairings.
```

When a listed TV matches a kept record again, the bridge uses the record and its pairings. It matches the TV's stored advertised identifiers first, then its name, then its address. An entry with `id=` never takes the records of a TV that stores other advertised identifiers. An entry with a name never takes the records of a TV with another name, unless the entry's `id=` matches one of the TV's stored advertised identifiers. A new TV at a kept TV's address therefore needs its own name.

Add an advertised identifier of each TV to `TV_USE_TVS` after you pair it, because it is the strongest match. To find it, read `identifiers` from `tv://tvs` or `GET /api/tvs` after pairing. Add `id=<first advertised identifier>` to the entry and restart. A later address change then needs only a new `host=`.

To delete a TV's records, send `DELETE /api/tvs/<record ID>` with the token. This also works for a kept TV, with the record ID from the log. A TV that `TV_USE_TVS` still names returns without pairings at the next start.

### How the container chooses its mode

1. `--standalone`, or `TV_USE_MODE=standalone` or `TV_USE_MODE=ha`, chooses the mode.
2. Otherwise, a token from the Supervisor chooses Home Assistant mode.
3. Otherwise, any other `TV_USE_` variable chooses standalone mode, even if `/data/options.json` exists.
4. Otherwise, `/data/options.json` chooses Home Assistant mode. Without it, the container uses standalone mode.

The log names the mode and the reason. Standalone mode never contacts the Supervisor, even if `SUPERVISOR_TOKEN` is set. Home Assistant mode accepts no command flags.

## Pair a TV

Pair through the `tv_pair` tool. Standalone mode has no pairing panel, because a browser page cannot send the bearer token. Each pairing needs a person who can read a code on the TV. Each PIN exchange expires after 180 seconds.

The TV lists the container as **Agent Bridge** under **Remote App and Devices**. The container has its own identity in the volume. A TV that is also paired with the Home Assistant app lists two **Agent Bridge** entries.

1. **Control.** Call `tv_pair {"tv":"Living Room","kind":"control"}`. The TV shows a four-digit AirPlay code. Call `tv_pair` again with the same `tv` and `kind`, the returned `data.pairing_id` and `"code":"1234"`, using the digits on the TV as a string. The bridge then tests whether the AirPlay credentials also work for Companion, the remote control protocol.
2. **Companion, if needed.** If `tv_status` still reports a control problem after step 1, pair Companion through REST, with the token and `Content-Type: application/json`. Post `{"protocol":"companion"}` to `/api/tvs/<record ID>/pair/start`. Then post `{"pairing_id":"<returned ID>","pin":"<code on the TV>"}` to `/api/tvs/<record ID>/pair/finish`.
3. **Screen.** Call `tv_pair {"tv":"Living Room","kind":"screen"}`. If the state is `waiting_for_pairing_screen`, open **Settings → Remotes and Devices → Remote App and Devices** on the TV and leave it open. Poll with the same `tv`, `kind` and `pairing_id` until the state is `code_on_screen`. Then submit the six-digit code as a string.
4. Call `tv_status {"tv":"Living Room"}`. Control and screen pairing should both be `"ok"`.

Submit the digits once. If a code fails, start a new exchange and wait for a new code. Many failed attempts can make the TV refuse pairing until it restarts.

Pairing records live in the volume under `/data/tvs/<record ID>/`. Keep the volume to keep the pairings. `docker volume rm` deletes them.

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

An agent on another computer on the LAN uses `http://<Mac IP address>:8765/mcp`. This needs `-p 8765:8765` instead of `-p 127.0.0.1:8765:8765`. Use the same token. Send it only over a trusted network, because plain HTTP carries the token in clear text.

Scripts can POST tool arguments to `/api/tool/<name>` with `Content-Type: application/json`. Upload a ZIP to `/api/bundle` with `Content-Type: application/zip`. These are the same routes that the Home Assistant integration uses, under `/api/` instead of `/api/atv_bridge/`. The tools and results are the same as in [the MCP API](mcp-api.md).

## Networking

### On a Mac with OrbStack

OrbStack runs containers in a Linux virtual machine. The default bridge network gives the container its own addresses and its own loopback interface:

- `-p 127.0.0.1:8765:8765` opens port 8765 on the Mac's loopback only. To accept agents from the LAN, use `-p 8765:8765`. This needs the OrbStack setting **Expose ports to LAN** (`docker.expose_ports_to_lan`), which is on by default. Docker then also opens the port on IPv6 and resets those connections, because the server listens on IPv4 only, so `localhost` can fail. Connect to `127.0.0.1`.
- The stage on port 8766, Chromium's debugging port and the page proxy listen on the container's loopback. No program on the Mac can reach them. OrbStack also routes each container's own IP address to the Mac, but that address reaches only port 8765.
- Connections from the container to the LAN leave through the Mac's IP address.

Control, screen reading, screenshots and AirPlay mirroring with sound all work on the bridge network. In a test on 21 November 2026, one mirror session to an Apple TV 4K (2nd generation) ran for 6.5 minutes with page audio on, on a silent page:

- tvpipe kept one process, and its count of frames sent rose at each of 25 samples, 15 seconds apart.
- Seven `tv_show` calls changed the page. The first took 2.5 seconds, the others 0.6 to 1.4 seconds. After each change, a `tv_look` screenshot showed the new page.
- Text `tv_look` and `tv_page_run` worked throughout, and the TV acknowledged every `tv_remote` key.
- From the Mac, neither `127.0.0.1` nor the container's address reached the stage or Chromium's debugging port.

What does not cross the virtual machine:

- **Multicast.** Bonjour discovery finds no TVs. Give each TV's IP address in `TV_USE_TVS`. Queries sent straight to a known address work.
- **The Mac's address.** The container sees only its virtual address. `presentation_mode` `play_url` and `auto` send the TV a link to an MP4 file on that address, and the TV cannot reach it. Use `mirror`.

### Host networking

With `--network host`, the container shares the network of the OrbStack virtual machine, and OrbStack forwards every port that the container opens to the Mac. A port on `0.0.0.0` opens on every Mac interface, and a port on `127.0.0.1` opens on the Mac's loopback. Control, screen reading, screenshots and mirroring with sound work this way too.

Avoid host networking on a Mac. The container's loopback ports then open on the Mac's loopback, and every program on the Mac can reach them without the token:

- **Chromium's debugging port.** tvpipe starts Chromium with `--remote-debugging-port` and drives it through that port, so the bridge cannot close it or move it to a pipe. A program that connects controls the bridge's browser, and through it can read the container's files, such as the pairing records in `/data` and the token file. In testing, a program on the Mac read a file in `/data` this way.
- **The page proxy** forwards requests under the page network rules.
- **The stage** is reachable too. It requires a key that changes at each start, refuses other origins on its socket and cannot be framed. A program that uses the debugging port can still read the key from the browser's tab list.

Use host networking only on a computer where you trust every program and user.

### On Linux

Linux was not tested. There, `--network host` gives the container the host's own network, as in Home Assistant, so Bonjour discovery should work, and the same loopback exposure applies to every program on the host. For `play_url` or `auto`, also set `TV_USE_STAGE_LISTEN=0.0.0.0`, so that the TV can fetch the MP4 files from port 8766.

### Security

- **Port 8765** needs the token on every route, including `/api/health` and the event socket. With `--insecure-local`, it needs a loopback host name instead.
- **Web pages cannot use port 8765**, even with `--insecure-local`. The server refuses a request whose `Origin` is another site or `null`, a request whose `Sec-Fetch-Site` is `cross-site` or `same-site`, and a POST body that is not `application/json`, or `application/zip` for `/api/bundle`. A web page in a browser cannot send those without the server's permission, and the server gives none. A current browser sends `Sec-Fetch-Site` with every request, even an image load that has no `Origin`. A page on another loopback port counts as `same-site`. The event socket also refuses every `Origin` except the server's own, with the same scheme, host and port. Programs send neither header.
- **The stage**, port 8766, serves only the bridge's own browser. It requires a random key that changes at each start. Its socket also requires the stage's own origin, and no other page can frame it.
- **Chromium's debugging port and the page proxy** accept any client on the container's loopback. Bridge networking keeps them inside the container. Host networking exposes them. See [Host networking](#host-networking).
- **The token.** Prefer `TV_USE_TOKEN_FILE`. A `TV_USE_TOKEN` value is visible in `docker inspect` and in the container's `/proc/1/environ`. The bridge removes it from the environment of the processes that it starts, such as Chromium and tvpipe.
- **Plain HTTP.** Send the token only over a trusted network.
- **Pages** cannot reach the container's own addresses or loopback. On a Mac, the container cannot see the Mac's addresses, so it protects them only when `TV_USE_HOST_ADDRESSES` lists them. That check comes before `TV_USE_PAGE_NETWORK_ALLOW`, so no grant can reach a listed address. List the Mac's LAN address, and its tailnet address if the Mac runs Tailscale. A DHCP reservation keeps the LAN address fixed.
  - **LAN.** Grant exact `host:port` entries in `TV_USE_PAGE_NETWORK_ALLOW`. Grant a subnet that contains the Mac only when `TV_USE_HOST_ADDRESSES` lists the Mac's address. Otherwise a page can reach every service on the Mac that listens on the LAN.
  - **Tailnet.** Pages cannot reach tailnet addresses, `100.64.0.0/10` and `fd7a:115c:a1e0::/48`, unless `TV_USE_PAGE_NETWORK_ALLOW` grants them. The Home Assistant app allows them by default. The tailnet includes the Mac's own tailnet address when the Mac runs Tailscale. In testing, the container reached the Mac's AirPlay receiver ports on that address. Before you grant a tailnet range, list that address in `TV_USE_HOST_ADDRESSES`.
  - **IPv6.** The page rules treat a global IPv6 address as public internet, because the container cannot see the LAN's IPv6 prefix. If the container has IPv6 routing, a page can reach a LAN device, including the Mac, at its global IPv6 address. To block the LAN, list its IPv6 prefix in `TV_USE_HOST_ADDRESSES`. The tested network had no global IPv6, and the container could not reach public IPv6 addresses.

## Measured results

Measured on 21 November 2026 against an Apple TV 4K (2nd generation) on tvOS 27.2. The Mac was on Apple silicon with OrbStack 2.2.5, `--network host` and 16 virtual CPUs. The later bridge-network test gave similar `tv_show` times. The Home Assistant Green ran the app that was deployed that night. Both showed the same simple page at 1920x1080. Each value comes from one to three runs.

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
| The container exits at start | Read `docker logs tv-use`. A missing or short token, an empty TV list, a bad value or a flag in Home Assistant mode stops the container with a message. |
| `401` with `unauthorized` | Send `Authorization: Bearer <token>` with the token from `TV_USE_TOKEN` or `TV_USE_TOKEN_FILE`. With `--insecure-local`, connect to `localhost` or `127.0.0.1`. |
| `403` with `forbidden_origin` | The request came from a web page on another site or another loopback port, or from a sandboxed page that sends `Origin: null`. Call the API from a program. |
| `415` with `unsupported_media_type` | Send `Content-Type: application/json`, or `application/zip` to `/api/bundle`. |
| Connection reset on `localhost:8765` | Without host networking, connect to `127.0.0.1`. The server listens on IPv4 only. |
| `405` on `GET /mcp` | Expected. The server answers MCP with `POST` only. |
| `tv_not_found` or no TV in `tv://tvs` | Add the TV to `TV_USE_TVS` with its IP address and restart the container. Discovery does not work on a Mac. |
| A TV and its pairings are gone from `tv://tvs` after a change to `TV_USE_TVS` | Look for `Kept the records of TV` in `docker logs tv-use`. Correct the entry, or add the TV's advertised identifier as `id=` or its name as `name=`, and restart. The bridge then uses the kept pairings. |
| `tv_unreachable` | Wake the TV. Check that the address in `TV_USE_TVS` is current. The container does not find a TV that changed its address. |
| `waiting_for_pairing_screen` does not change | Open **Settings → Remotes and Devices → Remote App and Devices** on the TV and leave it open. |
| `sender_failed` on `tv_show` | Check that the TV is awake and that AirPlay **Allow Access** includes this network. Read `docker logs tv-use`. |
| `tailnet_dns_missing` | Ignore it unless pages use tailnet names. To load them, give the container a DNS server that resolves them, and grant their addresses in `TV_USE_PAGE_NETWORK_ALLOW`. |
| `blocked_address` on a tailnet URL | Standalone mode blocks tailnet addresses by default. Grant the address or range in `TV_USE_PAGE_NETWORK_ALLOW`. |
| The mirror stutters | Lower `TV_USE_MIRROR_FPS`, or set `TV_USE_MIRROR_SIZE=1280x720`. |

## Update and remove

- **Update.** Run `docker pull ghcr.io/neekolas/tv-use-bridge:latest`, then `docker rm -f tv-use` and the same `docker run` command. The volume keeps the pairings.
- **Remove.** Run `docker rm -f tv-use`. To delete the pairings, also run `docker volume rm tv-use-data`. To delete one TV's pairings instead, send `DELETE /api/tvs/<record ID>` first. On each TV, remove **Agent Bridge** under **Settings → Remotes and Devices → Remote App and Devices**.
