"""Authenticated access to the app's internal API."""

from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.exceptions import HomeAssistantError
from yarl import URL

from .const import DOMAIN


class BridgeError(HomeAssistantError):
    """The bridge could not complete the request."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"detail": detail},
        )


class UnsupportedInventory(BridgeError):
    """Older single-TV app without the receiver registry."""


class BridgeClient:
    def __init__(
        self,
        session: ClientSession,
        config: Mapping[str, Any],
        tv_id: str | None = None,
    ) -> None:
        self.tv_id = tv_id
        self.config = config
        self.session = session
        self.base_url = URL.build(
            scheme="http", host=config["host"], port=config["port"]
        )
        self.headers = {"Authorization": f"Bearer {config['token']}"}

    def for_tv(self, tv_id):
        return BridgeClient(self.session, self.config, tv_id)

    def path(self, path):
        if self.tv_id:
            return f"/api/tvs/{self.tv_id}/" + path.removeprefix("/api/")
        return path

    async def inventory(self):
        try:
            return (await self.request("/api/tvs"))["tvs"]
        except UnsupportedInventory:
            return None

    async def request(
        self, path: str, body: dict | None = None, *, check: bool = True, method: str | None = None
    ) -> dict:
        try:
            async with self.session.request(
                method or ("GET" if body is None else "POST"),
                self.base_url.with_path(self.path(path)),
                json=body,
                headers=self.headers,
                timeout=ClientTimeout(
                    total=300 if path.startswith("/api/tool/") else 60, sock_connect=8
                ),
                allow_redirects=False,
            ) as response:
                if path == "/api/tvs" and response.status == 404:
                    raise UnsupportedInventory("Single-TV bridge")
                result = await response.json()
                if not isinstance(result, dict) or not isinstance(
                    result.get("ok"), bool
                ):
                    raise BridgeError("Invalid response from the bridge")
                # Preserve classified failures for diagnostics and LLM text results.
                # Authentication and redirects indicate that this is not a usable endpoint.
                if response.status in (401, 403) or 300 <= response.status < 400:
                    raise BridgeError(f"Bridge returned HTTP {response.status}")
                if result["ok"]:
                    if response.status >= 400:
                        raise BridgeError(f"Bridge returned HTTP {response.status}")
                elif check:
                    error = result.get("error") or {}
                    raise BridgeError(
                        error.get("message")
                        or result.get("state")
                        or f"HTTP {response.status}"
                    )
                return result
        except (ClientError, TimeoutError, ValueError) as err:
            raise BridgeError(f"Bridge request failed ({type(err).__name__})") from err

    async def tool(self, name: str, **arguments: Any) -> dict:
        result = await self.request(f"/api/tool/{name}", arguments)
        if result.get("error", {}).get("code") == "uncertain":
            raise BridgeError("Command delivery is uncertain")
        return result
