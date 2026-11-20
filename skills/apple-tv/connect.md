# Install and connect

## Install on Home Assistant OS

Use an Apple TV 4K running **tvOS 27 or later**. tvOS 27 or later is recommended and tested; tvOS 26.6 also works but is less tested. The bridge needs no Apple Developer account. Put the TV and the Home Assistant OS host on the same LAN. On the TV, enable AirPlay, leave its password off and set **Settings → AirPlay and Apple Home → Allow Access → Anyone on the Same Network**. Bonjour discovery works without a static IP. An optional DHCP reservation can help the bridge find a sleeping receiver.

Use an aarch64 or amd64 Home Assistant OS host. The integration test environment uses Home Assistant 2026.11.2. On a workstation with Git, `uv`, `rsync` and OpenSSH, open the repository root. For a fresh checkout:

```sh
git clone https://github.com/neekolas/ha-apple-tv.git
cd ha-apple-tv
```

Set `HA_URL` to the origin you use to open Home Assistant. Include the scheme and any non-default port. Use HTTPS, a trusted LAN or an encrypted tunnel for the admin token.

```sh
export HA_URL='http://homeassistant.local:8123'
mkdir -p ~/.config/atv-bridge
touch ~/.config/atv-bridge/ha_token
chmod 600 ~/.config/atv-bridge/ha_token
```

Sign in to Home Assistant as an administrator. Open your profile → **Security → Long-lived access tokens → Create token**. Save the token as one line in the file above with a local editor. Keep it out of chat, logs and version control.

```sh
uv run tools/deploy/deploy.py --url "$HA_URL" check
uv run tools/deploy/deploy.py --url "$HA_URL" ssh-setup
uv run tools/deploy/deploy.py --url "$HA_URL" push-app
uv run tools/deploy/deploy.py --url "$HA_URL" push-integration
```

These steps configure Terminal & SSH, stage the vendored pyatv fork, build and start the app, install the integration and restart HA Core. If HA's built-in Apple TV integration controls the same receiver, disable that receiver's entry under **Settings → Devices & services → Apple TV**.

1. Keep the TV awake. Under **Settings → Devices & services**, open the discovered **Apple TV Agent Bridge** and submit **Pair the discovered bridge**. Choose the receiver if prompted. Enter the four-digit AirPlay code from that TV. If HA offers optional Companion pairing, complete it for remote control.
2. For screen access, open **Settings → Remotes and Devices → Remote App and Devices** on the TV. Accept screenshot pairing in HA and enter the six-digit code. Screen access supplies both labels and images.
3. To register another receiver, choose **Add Apple TV** on the existing bridge entry. Select the receiver and follow its pairing prompts.

For manual installation and optional Tailscale setup, see the [setup guide](../../docs/SETUP.md).

## Connect an MCP client

Set the transport to **Streamable HTTP**. Append `/api/atv_bridge/mcp` to your HA origin. Send `Authorization: Bearer <HA admin token>` as a header. Use the public HA endpoint for all receivers. If using Tailscale, the origin can use your HA node's tailnet hostname or an address reached through an approved subnet route.

For Claude Code, using the token file above:

```sh
export HA_TOKEN="$(cat ~/.config/atv-bridge/ha_token)"
claude mcp add --transport http apple-tv "$HA_URL/api/atv_bridge/mcp" \
  --header "Authorization: Bearer $HA_TOKEN"
```

For a client that accepts an `mcpServers` JSON object, replace the sample origin and token using that client's secret configuration.

```json
{
  "mcpServers": {
    "apple-tv": {
      "type": "http",
      "url": "http://homeassistant.local:8123/api/atv_bridge/mcp",
      "headers": {"Authorization": "Bearer <HA admin token>"}
    }
  }
}
```

## Check the room

Read `tv://tvs` or call `tv_status {}`. Each inventory row has a stable bridge `id`, a `name`, advertised `identifiers`, `paired` (saved AirPlay record) and `screen_paired` (saved screen record). These flags show saved credentials. Use status to check whether they work.

Every tool accepts optional `tv`. A name, stable ID or advertised identifier matches without regard to case. Use the stable ID for duplicate names. With one registered TV, or exactly one with a saved AirPlay pairing, that receiver is the default. Otherwise, unqualified `tv_status` and `tv_look` summarize all registered TVs in `data.tvs`. Check each row's `ok` and `status` or `screen`. Other tools require a selector in that case.

Call `tv_status {"tv":"<room>"}` for the target. Check `data.tv.reachable`, `data.pairings`, `data.capabilities` and the relevant entries in `data.problems`. For an awake, screen-paired TV, `tv_look {"tv":"<room>"}` reads the labels. Use the same selector for later actions. See [troubleshooting](troubleshooting.md).

## Scripts

Send the arguments as JSON to `POST /api/atv_bridge/tool/<name>`. To check the connection with the environment variables above:

```sh
curl --fail-with-body -sS -H "Authorization: Bearer $HA_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "$HA_URL/api/atv_bridge/tool/tv_status"
```

Check both HTTP status and the JSON `ok` field. If a script requests a screenshot, the response puts a base64 PNG in `data.image` with `mimeType` and `data`. MCP clients receive a separate image content item. ZIP uploads use the [bundle endpoint](content.md).

## Built-in Home Assistant tools

The integration contributes six text tools to the Home Assistant LLM API and Assist. Each takes the public tool's arguments, including optional `tv`.

| Home Assistant tool | Bridge tool |
|---|---|
| `atv_bridge__show` | `tv_show` |
| `atv_bridge__play` | `tv_play` |
| `atv_bridge__open` | `tv_open` |
| `atv_bridge__power` | `tv_power` |
| `atv_bridge__status` | `tv_status` |
| `atv_bridge__pair` | `tv_pair` |

For Assist, expose the target room's bridge media player under **Settings → Voice assistants → Expose**. Assist limits selection to exposed rooms. With just one exposed receiver it selects that room. Replacing a mirror across rooms requires access to all receivers.
