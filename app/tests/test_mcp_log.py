import json

from atv_bridge_app.mcp_http import PEEK_LIMIT, describe


def test_describe_names_method_and_tool_without_arguments():
    body = json.dumps({"method": "tools/call", "params": {"name": "tv_show", "arguments": {"html": "secret"}}}).encode()
    assert describe("POST", body) == "POST tools/call tv_show"
    assert describe("POST", b'{"method":"initialize","params":{}}') == "POST initialize"
    assert describe("POST", b"x" * (PEEK_LIMIT + 1)) == f"POST {PEEK_LIMIT + 1} bytes"
    assert describe("POST", b"not json") == "POST unparsed"
    assert describe("DELETE", b"") == "DELETE"
