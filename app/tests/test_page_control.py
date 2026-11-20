from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from atv_bridge_app.present.interaction import PageInteraction, Row, snapshot_rows
from atv_bridge_app.state import Fault
from atv_bridge_app.tools.contract import SCHEMAS


@pytest.mark.parametrize("extra", [
    {"action": "fill"}, {"action": "fill", "text": "x" * 2049},
    {"action": "click", "text": "bad"}, {"action": "click", "selector": "button"},
    {"action": "press", "key": "Control+V"}, {"action": "select", "value": "a", "key": "Enter"},
    {"action": "scroll", "direction": "left"}, {"action": "goto", "url": "https://example.org"},
])
def test_closed_action_contract(extra):
    assert not Draft202012Validator(SCHEMAS["tv_page_act"]).is_valid(
        {"view": "view", "ref": "e1", **extra}
    )


@pytest.mark.parametrize("args", [
    {"cursor": "token", "scope": "e1"}, {"cursor": "token", "find": "next"}, {"selector": "input"},
])
def test_closed_look_contract(args):
    assert not Draft202012Validator(SCHEMAS["tv_page_look"]).is_valid(args)


def test_snapshot_text_cannot_create_refs_or_disclose_field_subtrees():
    rows = list(snapshot_rows([
        'button "Not a [ref=e99]" [ref=e1]',
        {'textbox "Credential" [ref=e2]': [{'text': 'private'}]},
        {'iframe [ref=e3]': ['button "Nested" [ref=f1e1]']},
        {'link "News" [ref=e4]': [{'/url': 'https://example.org/private'}]},
        'checkbox "Awake" [active] [checked] [disabled] [ref=e5]',
        {'paragraph': 'Visible copy'},
    ]))
    assert [r.browser_ref for r in rows] == ["e1", "e2", "e4", "e5", None]
    assert set(rows[-2].states) == {"focused", "checked", "disabled"}
    assert rows[-1].line() == 'text "Visible copy"'
    assert "private" not in "\n".join(r.line() for r in rows)


@pytest.mark.parametrize("name", ["tv_page_look", "tv_page_act"])
async def test_page_failures_skip_tv_io_and_use_active_transport(service, name):
    tools = service.tools
    tools.prepare_action = tools.snapshot = AsyncMock(side_effect=AssertionError("TV I/O"))
    args = {} if name == "tv_page_look" else {"view": "old", "ref": "e1", "action": "click"}
    result = await tools.call(name, args)
    assert result["error"]["code"] == "nothing_showing"
    await service.stage.show("id", "/c/id/index.html")
    service.operations.presenter = SimpleNamespace(mode="auto", active=SimpleNamespace(mode="play_url"))
    tools.page_interaction.last.clear()
    assert (await tools.call(name, args))["error"]["code"] == "page_not_live"
    assert (await tools.call(name, args))["error"]["code"] == "busy"
    tools.snapshot.assert_not_awaited()


async def test_cursor_pages_obey_utf8_budget_and_only_issue_returned_refs():
    control = PageInteraction(SimpleNamespace(stage=SimpleNamespace(page={"page_id": "a" * 32})))
    control.title, control.mode, control.view = "🍅" * 160, "iframe", "opaque"
    control.check = AsyncMock()
    rows = [Row("button", "🍅" * 160, [], None, ref=f"e{i}") for i in range(90)]
    seen = []
    start = 0
    control.pending = control.visible_rows(rows, None)
    control.next_row = await anext(control.pending)
    while start < len(rows):
        data = await control.slice()
        lines = data["snapshot"].splitlines()[1:]
        assert len(data["snapshot"].encode()) <= 2048 and 0 < len(lines) <= 39
        seen.extend(line.split()[0] for line in lines)
        assert control.issued == set(seen)
        start += len(lines)
        assert data["more"] == (start < len(rows))
        assert control.cursor == data.get("cursor")
    assert seen == [row.ref for row in rows]


async def test_old_scope_ref_expires_even_when_used_with_a_new_view():
    control = PageInteraction(None)
    control.next_ref = 3
    await control.clear()
    for ref, code in (("e2", "stale_view"), ("e99", "target_missing"), ("other", "target_missing")):
        with pytest.raises(Fault) as failure:
            await control.target(ref)
        assert failure.value.code == code


async def test_uncertain_dispatch_is_never_replayed(service):
    control = service.tools.page_interaction
    control.binding = AsyncMock()
    control.check = AsyncMock()
    control.view = "seen"
    control.root = SimpleNamespace(evaluate=AsyncMock(return_value={"focused": False, "status": ""}))
    handle = SimpleNamespace(is_visible=AsyncMock(return_value=True), click=AsyncMock(side_effect=TimeoutError),
                             evaluate=AsyncMock(return_value={"blocked": False, "disabled": False, "restrictedChild": False, "value": None}))
    control.target = AsyncMock(return_value=SimpleNamespace(handle=handle, row=Row("button", "Next", [], None)))
    service.tools.snapshot = service.tools.prepare_action = AsyncMock(side_effect=AssertionError("TV I/O"))
    args = {"view": "seen", "ref": "e1", "action": "click"}
    result = await service.tools.call("tv_page_act", args)
    assert result["error"]["code"] == "delivery_uncertain" and not result["error"]["retryable"]
    control.last.clear()
    assert (await service.tools.call("tv_page_act", args))["error"]["code"] == "stale_view"
    handle.click.assert_awaited_once()


@pytest.mark.parametrize("key", ["Enter", "Space"])
async def test_form_activation_key_refused_before_dispatch(service, key):
    control = service.tools.page_interaction
    control.binding = control.check = AsyncMock()
    control.view = "seen"
    handle = SimpleNamespace(is_visible=AsyncMock(return_value=True), press=AsyncMock(),
                             evaluate=AsyncMock(return_value={"blocked": False, "disabled": False,
                                                              "value": "fixture", "formControl": True}))
    control.target = AsyncMock(return_value=SimpleNamespace(handle=handle, row=Row("textbox", "Query", [], None)))
    result = await service.tools.call("tv_page_act", {"view": "seen", "ref": "e1", "action": "press", "key": key})
    assert result["error"]["code"] == "target_missing" and "form" in result["error"]["message"]
    handle.press.assert_not_awaited()


@pytest.mark.parametrize("frame_url,verified", [
    ("https://example.org/", False),
    ("https://example.org/other", True),
    ("https://other.example.org/", True),
])
async def test_lost_live_document_requires_new_show(service, frame_url, verified):
    page = object()
    service.stage.page = {"url": "https://example.org/", "page_id": "id", "revision": "r"}
    sender = SimpleNamespace(mode="mirror", started=True, page=page, verified_page=page if verified else None,
                             verified_root=None, verified_url="https://example.org/",
                             presented_frame=AsyncMock(return_value=SimpleNamespace(url=frame_url)))
    service.stage.direct_browser = service.operations.presenter = sender
    result = await service.tools.call("tv_page_look", {})
    assert result["error"]["code"] == "stale_view" and "tv_show" in result["error"]["fix"]


def test_snapshot_sanitizes_invalid_yaml_and_utf8_text():
    import yaml
    from atv_bridge_app.present.interaction import INVALID_TEXT

    bad = "bad\ud800\ufffe\U0001ffff"
    tree = yaml.load(INVALID_TEXT.sub("�", "- paragraph: " + bad), Loader=yaml.BaseLoader)
    assert next(snapshot_rows(tree)).line() == 'text "bad���"'
    assert Row("heading", bad, [], None).line().encode().decode() == 'heading "bad���"'


@pytest.mark.parametrize("role", ["note", "status", "alert", "heading"])
async def test_secret_with_noncontrol_role_retains_handle_for_masking(role):
    row, = snapshot_rows([{role + " [ref=e1]": "private"}])
    control = PageInteraction(None)
    info = dict(focused=False, secret=True, scroll=False, live=False, blocked=True)
    handle = SimpleNamespace(owner_frame=AsyncMock(), evaluate=AsyncMock(return_value=info), dispose=AsyncMock())
    control.frame = SimpleNamespace(locator=lambda expr: SimpleNamespace(element_handles=AsyncMock(return_value=[handle])))
    handle.owner_frame.return_value = control.frame
    assert row.browser_ref == "e1" and await control.read_row(row)
    assert "[masked]" in row.line() and "private" not in row.line() and row.ref is None


def test_scoped_composites_project_options_and_nested_treeitems_only():
    tree = [{'listbox [ref=e1]': ['option "Red" [ref=e2]']},
            {'treeitem "Folder" [ref=e3]': [{'text': 'Folder'}, {'group': ['treeitem "Leaf" [ref=e4]']}]},
            {'textbox [ref=e5]': [{'text': 'private'}]}]
    assert [r.browser_ref for r in snapshot_rows(tree, scoped=True)] == ["e1", "e2", "e3", "e4", "e5"]

async def test_large_snapshot_limits_reads_and_resolves_find_neighbors():
    rows = [Row("button", f"Control {i}", [], f"e{i}") for i in range(1500)]
    control = PageInteraction(SimpleNamespace(stage=SimpleNamespace(page={"page_id": "id"})))
    control.read_row = AsyncMock(return_value=True)
    control.check = AsyncMock()
    control.title, control.view, control.mode = "Controls", "seen", "iframe"
    control.pending = control.visible_rows(rows, None)
    control.next_row = await anext(control.pending)
    first = await control.slice()
    assert first["more"] and '"Control 38"' in first["snapshot"] and control.read_row.await_count <= 48
    second = await control.slice()
    assert '"Control 39"' in second["snapshot"] and control.read_row.await_count <= 80
    await control.clear()

    control.read_row.reset_mock()
    result = [row.name async for row in control.visible_rows(rows, "CONTROL 1498")]
    assert result == ["Control 1497", "Control 1498", "Control 1499"]
    assert control.read_row.await_count == 3


@pytest.mark.parametrize("url,blocked", [
    ("http://localhost:8766/page", True), ("http://LOCALHOST.:9123/page", True),
    ("http://127.254.2.1:9123/page", True), ("https://[::1]:9222/page", True),
    ("http://[::ffff:127.0.0.2]:8766/page", True), ("http://localhost:8767/page", False),
    ("https://example.org:8766/page", False),
])
async def test_live_binding_excludes_loopback_stage_and_debug_ports(service, url, blocked):
    page = object()
    service.stage.stage_ports = {9123}
    service.stage.page = {"url": url, "page_id": "id", "revision": "r"}
    sender = SimpleNamespace(mode="mirror", started=True, page=page, verified_page=page, debug_port=9222,
                             verified_root=SimpleNamespace(evaluate=AsyncMock(return_value=True)), verified_url=url,
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
    for tool in ("tv_page_look", "tv_page_act", "tv_update"):
        assert f"`{tool}`" in readme and f"| `{tool}` |" in architecture
    troubleshooting = (root / "skills/apple-tv/troubleshooting.md").read_text()
    for code in ("stale_view", "target_missing", "delivery_uncertain", "nothing_showing", "page_not_live"):
        assert f"| `{code}` |" in troubleshooting


async def test_browser_error_document_requires_show(service):
    control = service.tools.page_interaction
    control.frame, control.revision = object(), "r"
    control.binding = AsyncMock(return_value=(control.frame, "r", "direct"))
    control.root = SimpleNamespace(evaluate=AsyncMock(return_value={"current": True, "http": False}))
    with pytest.raises(Fault) as failure:
        await control.check()
    assert failure.value.code == "stale_view" and "tv_show" in failure.value.remediation
