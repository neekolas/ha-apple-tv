from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from atv_bridge_app.state import Fault
from atv_bridge_app.tools.contract import SCHEMAS


async def test_page_failures_skip_tv_io_and_use_active_transport(service):
    name = "tv_page_run"
    tools = service.tools
    tools.prepare_action = tools.snapshot = AsyncMock(side_effect=AssertionError("TV I/O"))
    args = {"script": "document.title"}
    result = await tools.call(name, args)
    assert result["error"]["code"] == "nothing_showing"
    await service.stage.show("id", "/c/id/index.html")
    service.operations.presenter = SimpleNamespace(mode="auto", active=SimpleNamespace(mode="play_url"))
    tools.page_interaction.last_run = float("-inf")
    assert (await tools.call(name, args))["error"]["code"] == "page_not_live"
    assert (await tools.call(name, args))["error"]["code"] == "busy"
    tools.snapshot.assert_not_awaited()


@pytest.mark.parametrize("url", ["https://example.org/next", "https://other.example.org/"])
async def test_live_binding_follows_the_presented_frame_after_navigation(service, url):
    service.stage.page = {"url": "https://example.org/", "page_id": "id", "revision": "r"}
    frame = SimpleNamespace(url=url)
    sender = SimpleNamespace(mode="mirror", started=True, debug_port=None, presented_frame=AsyncMock(return_value=frame))
    service.stage.direct_browser = service.operations.presenter = sender
    assert await service.tools.page_interaction.binding() == (frame, frame.url, True)


@pytest.mark.parametrize("url,blocked", [
    ("http://localhost:8766/page", True), ("http://LOCALHOST.:9123/page", True),
    ("http://127.254.2.1:9123/page", True), ("https://[::1]:9222/page", True),
    ("http://[::ffff:127.0.0.2]:8766/page", True), ("http://localhost:8767/page", False),
    ("https://example.org:8766/page", False), ("http://alias.test:9123/page", True),
    ("http://0.0.0.0:8765/page", True),
])
async def test_live_binding_excludes_loopback_stage_and_debug_ports(service, url, blocked):
    page = object()
    service.stage.stage_ports = {9123}
    service.stage.page = {"url": url, "page_id": "id", "revision": "r"}
    sender = SimpleNamespace(mode="mirror", started=True, page=page, debug_port=9222,
                             document_peer=AsyncMock(return_value="127.0.0.1" if "alias.test" in url else "192.0.2.8"),
                             presented_frame=AsyncMock(return_value=SimpleNamespace(url=url)))
    service.stage.direct_browser = service.operations.presenter = sender
    if blocked:
        with pytest.raises(Fault) as failure:
            await service.tools.page_interaction.binding()
        assert failure.value.code == "target_missing" and "tv_show" in failure.value.remediation
    else:
        frame, _, _ = await service.tools.page_interaction.binding()
        assert frame.url == url


def test_page_control_reference_covers_tools_and_recovery():
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text()
    architecture = (root / "docs/ARCHITECTURE.md").read_text()
    assert "`tv_page_run`" in readme and "| `tv_page_run` |" in architecture
    troubleshooting = (root / "skills/apple-tv/troubleshooting.md").read_text()
    for code in ("stale_view", "target_missing", "script_error", "scripts_disabled", "nothing_showing", "page_not_live"):
        assert f"| `{code}` |" in troubleshooting


async def test_browser_error_document_requires_show(service):
    control = service.tools.page_interaction
    frame = SimpleNamespace(url="https://example.org/page", evaluate=AsyncMock(return_value={"changed": "chrome-error://chromewebdata/"}))
    control.binding = AsyncMock(return_value=(frame, frame.url, True))
    service.operations.presenter = SimpleNamespace(debug_port=None)
    with pytest.raises(Fault) as failure:
        await control.run("document.title")
    assert failure.value.code == "target_missing" and "tv_show" in failure.value.remediation


@pytest.mark.parametrize("script,arg,expected", [
    ("arg.x + 1", {"x": 4}, 5),
    ("arg.x + 1;", {"x": 4}, 5),
    ("async arg => await Promise.resolve(arg)", ["ok"], ["ok"]),
    ("return await Promise.resolve(arg);", {"ok": True}, {"ok": True}),
    ("throw new Error('fixture error')", None, "script_error"),
    ("'🍅'.repeat(20000)", None, "truncated"),
])
async def test_page_script_includes_browser_serialization(service, script, arg, expected):
    import asyncio
    import json
    import playwright
    from atv_bridge_app.present.interaction import RUN_SCRIPT, SCRIPT_LIMIT

    # Execute the shipped browser function with Playwright's bundled JS runtime.
    # The live check separately proves Chromium frame and document identity.
    node = Path(playwright.__file__).parent / "driver" / "node"

    async def evaluate(expression, options):
        assert expression == RUN_SCRIPT
        js = ("global.document={documentURI:'https://example.org/page'};"
              + "(" + expression + ")(" + json.dumps(options) + ").then(v=>process.stdout.write(JSON.stringify(v)))")
        proc = await asyncio.create_subprocess_exec(str(node), "-e", js, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        assert proc.returncode == 0
        return json.loads(stdout)

    frame = SimpleNamespace(url="https://example.org/page", evaluate=evaluate)
    control = service.tools.page_interaction
    service.stage.page = {"page_id": "id", "revision": "r"}
    service.operations.presenter = SimpleNamespace(debug_port=None)
    control.binding = AsyncMock(return_value=(frame, frame.url, True))
    result = await service.tools.call("tv_page_run", {"script": script, "arg": arg})
    if expected == "script_error":
        assert result["error"]["code"] == expected and result["error"]["message"] == "fixture error"
        assert "fixture error" in result["data"]["stack"]
    elif expected == "truncated":
        data = result["data"]
        assert data["truncated"] and len(data["preview"].encode()) <= SCRIPT_LIMIT < data["bytes"]
    else:
        assert result["ok"] and result["data"] == {"result": expected, "truncated": False}


async def test_page_script_option_and_deadline(service):
    import asyncio

    control = service.tools.page_interaction
    args = {"script": "1", "timeout_ms": 10}
    service.tools.page_scripts = False
    control.binding = AsyncMock()
    result = await service.tools.call("tv_page_run", args)
    assert result["error"]["code"] == "scripts_disabled" and "page_scripts" in result["error"]["fix"]
    control.binding.assert_not_awaited()
    service.tools.page_scripts = True

    async def wait_for_document():
        await asyncio.sleep(1)

    control.binding.side_effect = wait_for_document
    result = await service.tools.call("tv_page_run", args)
    assert result["error"]["code"] == "script_error" and "10 ms" in result["error"]["message"]
    assert not Draft202012Validator(SCHEMAS["tv_page_run"]).is_valid({**args, "timeout_ms": 30001})
