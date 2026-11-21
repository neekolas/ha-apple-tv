# Home Assistant Assist and LLM tools

The integration adds six text tools to the Home Assistant LLM API and Assist. Each takes the public tool's arguments, including the optional `tv`.

| Home Assistant tool | Bridge tool |
|---|---|
| `atv_bridge__show` | `tv_show` |
| `atv_bridge__play` | `tv_play` |
| `atv_bridge__open` | `tv_open` |
| `atv_bridge__power` | `tv_power` |
| `atv_bridge__status` | `tv_status` |
| `atv_bridge__pair` | `tv_pair` |

For Assist, expose each room's bridge media player under **Settings → Voice assistants → Expose**. Assist can select only exposed rooms. With one exposed TV, it selects that TV. Replacing a page on another room's TV needs access to all TVs.

The administrative Home Assistant LLM API gets these tools when the integration entry is loaded. External agents should use the MCP endpoint instead; see the **tv-use-connect** skill.
