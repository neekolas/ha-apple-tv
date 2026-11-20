# Apple TV Agent Bridge integration

Install the bridge app and this integration on the same Home Assistant OS host. The app controls the TVs. The integration provides pairing forms, one device per receiver, health entities, and an authenticated endpoint for agents. The bridge supports tvOS 27 or later and needs no Apple Developer account. [Install the app and integration](../../app/DOCS.md)

For manual installation, copy this directory, including `manifest.json` and `translations/`, to `custom_components/atv_bridge/` under the HA configuration directory. Restart HA Core. Start or restart the bridge app so Supervisor sends discovery. In **Settings → Devices & services**, configure the discovered **Apple TV Agent Bridge**. For manual entry on the same host, use `127.0.0.1`, port `8765` and the app's `/data/internal_token`.

Select the receiver if prompted. Enter the four-digit AirPlay code as text. Complete Companion pairing if offered. To enable screen reads, open **Settings → Remotes and Devices → Remote App and Devices** on the TV before starting screen pairing, then enter the six-digit code. Preserve leading zeros. To add another room, choose **Add Apple TV** on the existing entry. Use **Configure** for room-specific control or screen repair, or to choose a phone for pairing notifications.

## Connect an agent

Set `HA_URL` to the actual HA origin, including the port if needed. Create a long-lived access token in an administrator's HA profile under **Security** and supply it as `HA_TOKEN`. Agents use the HA token. The integration adds the internal token when forwarding requests.

```sh
export HA_URL='http://<ha-host>:8123'  # Replace with your HA origin.
# If you used the repository deployer, reuse its admin token.
export HA_TOKEN="$(cat ~/.config/atv-bridge/ha_token)"

curl -fsS -H "Authorization: Bearer $HA_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' "$HA_URL/api/atv_bridge/tool/tv_status"
```

Use Streamable HTTP with `$HA_URL/api/atv_bridge/mcp` and the same bearer header. For example, in Claude Code:

```sh
claude mcp add --transport http apple-tv "$HA_URL/api/atv_bridge/mcp" \
  --header "Authorization: Bearer $HA_TOKEN"
```

Read `tv://tvs` for registered names and IDs. All ten bridge tools accept optional `tv`. With one paired receiver you can omit it. With several, use a receiver-specific call such as `tv_look {"tv":"<room>"}`. The default look is text only. An unscoped `tv_status {}` returns room summaries when the target is ambiguous.

Scripts can POST tool arguments as JSON to `/api/atv_bridge/tool/<name>`. To upload a bundle, send a ZIP as the body. Put `index.html` at its root. Both the ZIP and its unpacked contents must fit within 50 MiB.

```sh
curl -fsS -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/zip' \
  --data-binary @page.zip "$HA_URL/api/atv_bridge/bundle"
```

Use the returned identifier with `tv_show {"tv":"<room>","bundle":"<bundle>"}`. Inspect `ok`, `verified` and `error.fix` when present. A 401 usually means missing or invalid HA credentials. A 403 requires an admin token. A 503 means the integration has no loaded entry. A 502 from the bridge proxy means it could not contact the app.

## Built-in Home Assistant LLM APIs

The integration adds six tools to the Home Assistant administrative LLM API when the entry is loaded. To make them available in Assist, expose the bridge media player for each intended TV in **Settings → Voice assistants → Expose**.

The names are `atv_bridge__show`, `atv_bridge__play`, `atv_bridge__open`, `atv_bridge__power`, `atv_bridge__status` and `atv_bridge__pair`. Each accepts optional `tv`. Assist limits calls to exposed receivers. Replacing another room's presentation requires access to all receivers. HA returns the tool result as JSON text. Use the bridge MCP endpoint for screen reads, navigation, remote input or page updates.

## Integration tests

The test environment pins Home Assistant and `pytest-homeassistant` to 2026.11.2. It uses Python 3.14.2 or later in the 3.14 series. From the repository root, run

```sh
uv run --locked --project tests/integration pytest tests/integration
```
