# Connect an agent

How to register the tv-use MCP server in a client, choose a local or remote connection, check the rooms and call the tools from scripts. To install or pair the bridge, use the **tv-use-setup** skill.

The examples read the token from `~/.config/atv-bridge/ha_token`, a one-line file that only you can read. Create it with a local editor if you do not have it. Keep the token out of chat, logs and version control.

## Connect an MCP client

Set the transport to **Streamable HTTP**. Append `/api/atv_bridge/mcp` to your HA origin. Send `Authorization: Bearer <HA admin token>` as a header. One endpoint serves all receivers.

### Choose local or remote

Choose the connection by where the agent runs. The same token works for both.

| Agent runs | Origin | Notes |
|---|---|---|
| **Local connection:** on the home network, such as a desktop, a home server or a laptop at home | `http://homeassistant.local:<port>`, or HA's LAN IP address | Recommended. The fastest path, with no Tailscale. |
| **Remote connection:** outside the home, such as a cloud sandbox | HA's tailnet hostname, such as `http://homeassistant.<tailnet>.ts.net:<port>`, or a LAN address through an approved subnet route | Needs Tailscale on HA and on the agent machine. See the [setup guide](https://github.com/neekolas/tv-use/blob/main/docs/SETUP.md#tailscale-for-remote-agents). |

Use HA's web port in both. It is 8123 by default, but some installs serve HA on 80. On the home network, always use the local connection. To check for it, set `HA_PORT` to HA's web port and run this. It needs no token.

```sh
curl -sS -m 2 -o /dev/null -w '%{http_code}\n' "http://homeassistant.local:${HA_PORT:-8123}/api/"
```

`200` or `401` means HA answered, so the local connection works. A timeout, a refused connection or a name error means it does not. Some containers and Linux hosts cannot resolve `.local` names. On a machine at home, try HA's LAN IP address in that case.

On the home network the token travels over plain HTTP. Keep it on a trusted network. Tailscale encrypts the remote connection.

### Register the server

Set `HA_URL` to the origin you chose. For Claude Code, using the token file above:

```sh
export HA_TOKEN="$(cat ~/.config/atv-bridge/ha_token)"
claude mcp add --transport http apple-tv "$HA_URL/api/atv_bridge/mcp" \
  --header "Authorization: Bearer $HA_TOKEN"
```

For Codex, export `HA_TOKEN` in the shell that starts Codex. Codex reads the token from that variable on each start.

```sh
codex mcp add apple-tv --url "$HA_URL/api/atv_bridge/mcp" --bearer-token-env-var HA_TOKEN
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

Call `tv_status {"tv":"<room>"}` for the target. Check `data.tv.reachable`, `data.pairings`, `data.capabilities` and the relevant entries in `data.problems`. For an awake, screen-paired TV, `tv_look {"tv":"<room>"}` reads the labels. Use the same selector for later actions. See [troubleshooting.md](troubleshooting.md).

## Scripts

Send the arguments as JSON to `POST /api/atv_bridge/tool/<name>`. To check the connection with the environment variables above:

```sh
curl --fail-with-body -sS -H "Authorization: Bearer $HA_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "$HA_URL/api/atv_bridge/tool/tv_status"
```

Check both HTTP status and the JSON `ok` field. If a script requests a screenshot, the response puts a base64 PNG in `data.image` with `mimeType` and `data`. MCP clients receive a separate image content item. ZIP uploads use the bundle endpoint; see the **tv-use-web-apps** skill.
