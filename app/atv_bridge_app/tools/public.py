"""The v1 operation dispatcher shared by HTTP, MCP and Home Assistant."""

import asyncio
from pathlib import Path
from urllib.parse import quote, urlsplit

import yaml
from jsonschema import Draft202012Validator

from ..live import TAILNET_DNS_FIX
from ..present.media import MediaPlayback
from ..state import Fault, classify
from . import link_info, playback_identity
from .contract import SCHEMAS, UNKNOWN, checked, outcome, public_error, schema
from .sequence import run_sequence
from .deep_link import confirm_custom_link, link_targets, observed, title_action
from .pair import exchange
from .prompts import prompt_choice
from .navigation import label, match_app, matches_label, navigate, normal

RECIPES = yaml.safe_load((Path(__file__).parents[1] / "data/apps.yaml").read_text())


class PublicTools:
    def __init__(self, ops, mirror_size):
        self.ops, self.engine, self.stage = ops, ops.engine, ops.stage
        self.size = mirror_size
        self.lock = asyncio.Lock()
        self.wait_timeout = 10
        self.play_timeout = 20
        self.settle_ms = 300
        self.last_screen = UNKNOWN.copy()
        self.last_look = None
        self.profile_name = None
        self.action_evidence = []
        self.media = MediaPlayback(self.engine)

    async def snapshot(self, focus=None, read_focus=True, observation=None, *, status=None):
        if status is None:
            status = await self.engine.status()
        if read_focus and status["power"] not in ("off", "unknown"):
            try:
                observation = await self.ops.observer.call("ax_focused")
                focus = observation["focused"]
            except Fault:
                focus, observation = None, {"app": None}
        if not read_focus and observation is None:
            observation = {"app": None}
        app = await self.ops.foreground_app(status, focus, observation=observation)
        media = status.get("now_playing")
        page = self.stage.page
        self.last_screen = {
            "power": status["power"],
            "keyboard": status["keyboard_focused"],
            "focus": {"label": label(focus), "role": focus.get("role"), "value": focus.get("value")} if focus else None,
            "app": {"id": app["bundle_id"], "name": app["name"], "source": app["source"]}
            if app
            else None,
            "playing": {k: media.get(k) for k in ("title", "state", "position_s", "duration_s")}
            if media
            else None,
            "presenting": {
                "page_id": page["page_id"],
                "title": page.get("title"),
                "url": page.get("url"),
            }
            if page
            else None,
        }
        return self.last_screen.copy()

    def failure(self, name, fault):
        error = public_error(fault)
        result = outcome(
            error["message"],
            fault.fields.get("evidence", []),
            {k: v for k, v in fault.fields.items() if k != "evidence"},
            False,
        )
        result.update(ok=False, error=error)
        if name != "tv_status":
            result["screen"] = self.last_screen.copy()
        return result

    def audit_failure(self, name, args, fault):
        result = self.failure(name, fault)
        self.ops.state.audit(name, args, result)
        return result

    async def call(self, name, args, *, replace=False):
        spec = SCHEMAS.get(name, schema() if name == "tv_hide" else None)
        if spec is None or not Draft202012Validator(spec).is_valid(args):
            return self.audit_failure(
                name,
                args,
                Fault(
                    "invalid_arguments",
                    "The request does not match the tool schema.",
                    "Use tools/list for accepted arguments.",
                ),
            )
        try:
            await asyncio.wait_for(self.lock.acquire(), self.wait_timeout)
        except TimeoutError:
            return self.audit_failure(
                name,
                args,
                Fault(
                    "busy", "Another call is using the TV.", "Retry after the current call completes."
                ),
            )
        try:
            self.action_evidence = []
            if (
                name == "tv_show"
                and args
                and hasattr(self, "admit")
                and (args.get("url") or self.ops.sender.mode != "play_url")
            ):
                await self.admit(replace)
            already_off = name == "tv_power" and not args["on"] and (await self.engine.status())["power"] == "off"
            if name not in ("tv_look", "tv_status", "tv_pair", "tv_sequence") and not already_off:
                await self.prepare_action(force=name == "tv_power" and args["on"])
            result = await getattr(self, name)(**args)
            if self.action_evidence:
                result["evidence"][:0] = self.action_evidence
            if name != "tv_status" and "screen" not in result:
                result["screen"] = await self.snapshot()
            if name == "tv_look":
                self.last_look = {k: v for k, v in result.items() if k != "image"}
        except Exception as exc:  # noqa: BLE001 - Both public transports use this contract.
            fault = classify(exc)
            if name not in ("tv_status", "tv_pair"):
                await self.snapshot(
                    read_focus=fault.code
                    not in ("screen_unavailable", "observer_failed", "observer_unavailable")
                )
            result = self.failure(name, fault)
            if self.action_evidence:
                result["evidence"][:0] = self.action_evidence
            if name == "tv_pair":
                result["screen"] = UNKNOWN.copy()
        finally:
            if hasattr(self, "reconcile"):
                self.reconcile()
            self.lock.release()
        self.ops.state.audit(name, args, result)
        return result

    async def apps(self):
        try:
            await asyncio.wait_for(self.lock.acquire(), self.wait_timeout)
        except TimeoutError as exc:
            raise Fault(
                "busy", "Another call is using the TV.", "Retry after the current call completes.", True
            ) from exc
        try:
            return [
                {"id": a["bundle_id"], "name": a["name"]}
                for a in (await self.ops.tv_list_apps())["apps"]
            ]
        finally:
            self.lock.release()

    async def takeover(self):
        await self.media.stop()
        if self.stage.page:
            await self.ops.tv_hide()

    async def tv_power(self, on):
        """Turn the TV on or off and check its power state."""
        await self.takeover()
        if not on:
            await self.engine.sleep_verified()
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            screen = await self.snapshot(read_focus=on)
            if screen["power"] in (("on", "screensaver") if on else ("off",)):
                return outcome(
                    f"The TV is {'on' if on else 'off'}.", ["Power state reported by the TV"]
                ) | {"screen": screen}
            if asyncio.get_running_loop().time() >= deadline:
                raise Fault(
                    "uncertain",
                    "The TV did not confirm its power state.",
                    "Check tv_look before retrying.",
                )
            await asyncio.sleep(0.25)

    async def prepare_action(self, force=False):
        checked(await self.engine.ready(force=force))
        await self.prepare_ui()

    async def prepare_ui(self):
        if self.engine.last_power == "screensaver":
            await self.press("menu")
            for _ in range(8):
                if (await self.engine.status())["power"] == "on":
                    break
                await asyncio.sleep(0.25)
            else:
                raise Fault("uncertain", "The screen saver is still active.", "Read tv_look before acting.")
            self.action_evidence.append("Exited screen saver")
        await self.resolve_prompts(system=True)

    async def resolve_prompts(self, system=False):
        if not self.ops.state.receiver("remote_pairing"):
            return False
        changed = False
        previous = None
        for _ in range(4):
            try:
                view = await self.read_observation()
            except Fault:
                return changed
            preferred = self.default_profile if system else self.app_profile or self.profile_name or self.default_profile
            choice = prompt_choice(view, system=system, preferred=preferred)
            if not choice:
                return changed
            if choice == previous:
                raise Fault("uncertain", "The same prompt is still visible.", "Read tv_look before acting.")
            description, target = choice
            await navigate(self.observe, self.press, target)
            focus, _ = await self.observe()
            if normal(label(focus)) != normal(target):
                raise Fault("uncertain", "The intended prompt choice is not focused.", "Read tv_look.")
            await self.press("select")
            self.action_evidence.append(f"{description}: {target}")
            if system:
                self.profile_name = target
            previous, changed = choice, True
            await asyncio.sleep(0.35)
        raise Fault("uncertain", "Launch prompts did not settle.", "Read tv_look.")

    async def settings_root(self):
        previous = None
        for _ in range(10):
            view = await self.read_observation()
            items = [normal(label(i)) for i in view["items"]]
            if (view.get("app") or {}).get("bundle_id") != "com.apple.TVSettings" or not items:
                break
            if ("settings" in items and "remotes and devices" in items) or {"general", "network", "remotes and devices"} <= set(items):
                return
            if previous == items:
                break
            previous = items
            await self.press("menu")
            await asyncio.sleep(0.35)
        raise Fault("uncertain", "Could not verify the Settings root menu.", "Open Settings on the TV and retry the path.")

    async def tv_pair(self, kind, **args):
        """Pair control or screen for a registered TV. Poll with pairing_id, ask for digits at code_on_screen, then submit or cancel."""
        return await exchange(self, kind, **args)

    async def tv_status(self):
        """Report reachability, pairing, capabilities and current problems."""
        s = await self.engine.status()
        h = await self.ops.tv_health()
        p = h["pairings"]
        problems = []
        if not h["ok"]:
            e = h["error"]
            problems.append(public_error(Fault(e["code"], e["message"], e["remediation"])))
        can = s["connected"] and not problems
        problems.extend(h["problems"])
        screen_invalid = any(issue["code"] == "screen_pairing_invalid" for issue in problems)
        screen_ready = p["remote_pairing"] and not screen_invalid
        control_invalid = any(issue["code"] == "pairing_removed" for issue in problems)
        for issue in problems:
            if issue["code"] in ("not_paired", "pairing_removed"):
                issue["fix"] = 'Run tv_pair with kind "control" for this TV.'
        if not p["remote_pairing"]:
            problems.append(
                {
                    "code": "screen_unavailable",
                    "fix": 'Run tv_pair with kind "screen" for this TV.',
                }
            )
        if self.media.problem:
            problems.append(public_error(self.media.problem))
        if h["settings"]["magicdns"]["resolved"] is False:
            problems.append(
                {
                    "code": "page_unreachable",
                    "fix": TAILNET_DNS_FIX,
                }
            )
        return outcome(
            "The bridge checked the TV and its pairings.",
            h["evidence"],
            {
                "tv": {
                    **h["tv"],
                    "name": s.get("name"),
                    "model": s.get("model"),
                    "tvos": s.get("tvos"),
                    "reachable": s["connected"],
                },
                "tailnet_dns": h["tailnet_dns"],
                "browser": h["browser"],
                "pairings": {
                    "control": "invalid" if control_invalid else "ok" if p["companion"] else "missing",
                    "screen": "invalid" if screen_invalid else "ok" if screen_ready else "missing",
                },
                "capabilities": {
                    "show": bool(can and p["airplay"]),
                    "look_image": s["connected"] and screen_ready,
                    "look_text": s["connected"] and screen_ready,
                    "play_media": bool(
                        can
                        and p["airplay"]
                        and getattr(self.engine.adapter, "supports_play_url", False)
                    ),
                },
                "problems": problems,
            },
        )

    async def read_observation(self, *, single_read=False):
        try:
            observation = await self.ops.observer.call("ax_items", max_items=100)
            if single_read:
                return {"focused": None, "app": None} | observation
            focus = (
                observation["focused"]
                if "focused" in observation
                else (await self.ops.observer.call("ax_focused"))["focused"]
            )
            return observation | {"focused": focus}
        except Fault as exc:
            raise Fault(
                "screen_unavailable",
                "Accessibility text is unavailable.",
                "Check Remote Pairing in Home Assistant.",
            ) from exc

    async def observe(self):
        observation = await self.read_observation()
        return observation["focused"], observation["items"]

    async def tv_look(self, image=False, max_width=1280):
        """Read screen labels and focus, optionally capturing a screenshot."""
        observation = {"app": None}
        try:
            observation = await self.read_observation()
            focus, items = observation["focused"], observation["items"]
        except Fault:
            if not image:
                raise
            focus, items = None, []
        if not image and not focus and not items:
            raise Fault(
                "screen_unavailable",
                "The accessibility service returned no labels or focus.",
                "Wait for the app to settle and read tv_look again.",
                items=[],
            )
        if focus and not focus.get("role"):
            match = next((item for item in items if label(item) == label(focus)), {})
            focus = {**match, **focus}
        screen = await self.snapshot(focus, False, observation)
        data = {
            "items": [
                {
                    "label": label(i),
                    "role": i.get("role"),
                    "value": i.get("value"),
                    "focused": bool(focus)
                    and (
                        (i["uid"] == focus["uid"])
                        if i.get("uid") and focus.get("uid")
                        else normal(label(i)) == normal(label(focus))
                    ),
                }
                for i in items
            ],
            "page_state": await self.stage.current_state(),
        }
        result = outcome(
            "Read the TV screen.", ["Accessibility observation"] if focus or items else [], data
        ) | {"screen": screen}
        if image:
            try:
                capture = checked(await self.ops.tv_screenshot(max_width))
            except Fault as exc:
                raise Fault(
                    "screen_unavailable", "Screen capture failed.", "Check Remote Pairing and retry."
                ) from exc
            result["image"] = capture["image"]
            data.update(toast_visible=True, captured_at=capture["captured_at"])
            result["evidence"].append("DVT screenshot")
        return result

    async def press(self, key, hold=0, *, switching_app=False):
        return checked(await self.ops.tv_press([key], hold_ms=hold, settle_ms=self.settle_ms, switching_app=switching_app))

    @staticmethod
    def changed(before, after):
        return (
            any(before[k] != after[k] for k in ("power", "focus", "keyboard"))
            or (before["app"] or {}).get("id") != (after["app"] or {}).get("id")
            or playback_identity(before["playing"]) != playback_identity(after["playing"])
        )

    async def tv_remote(self, keys=None, text=None, submit=False, hold_ms=0, swipe=None):
        """Send one remote gesture or key at a time, or type into a focused field."""
        path = []
        screen = await self.snapshot()
        verified = True
        evidence = []
        if text is not None:
            checked(await self.ops.tv_type(text))
            evidence.append("Keyboard text matched readback")
            screen = await self.snapshot()
            keys = ["select"] if submit else []
        if swipe:
            try:
                async with self.engine.session() as client:
                    await client.swipe(swipe)
            except Exception as exc:
                raise Fault(
                    "uncertain", "Swipe delivery could not be confirmed.", "Read tv_look before acting."
                ) from exc
            after = await self.snapshot()
            verified = self.changed(screen, after)
            screen = after
        for index, key in enumerate(keys or []):
            try:
                await self.press(key, hold_ms)
            except Fault as exc:
                exc.fields.update(confirmed_keys=len(path), keys=path)
                raise
            path.append(key)
            after = await self.snapshot()
            if key in ("home", "menu", "select") and (
                not after["focus"] or not self.changed(screen, after)
            ):
                await asyncio.sleep(0.3)
                after = await self.snapshot()
            verified &= self.changed(screen, after)
            screen = after
            if index < len(keys) - 1 and screen["focus"] is None:
                raise Fault(
                    "uncertain",
                    "Focus could not be read between remote presses.",
                    "Read tv_look before sending the rest.",
                    confirmed_keys=len(path),
                    keys=path,
                )
        evidence.append(
            "Screen change observed" if verified else "Delivery acknowledged; screen change unconfirmed"
        )
        result = outcome(
            "The TV acknowledged the remote input.",
            evidence,
            {"keys": path, "delivered": "confirmed", "confirmed_keys": len(path)},
            verified,
        )
        return result | ({"screen": screen} if screen else {})

    async def tv_sequence(self, steps, for_each=None):
        """Run bounded remote steps on one receiver, stopping at the first failure."""
        return await run_sequence(self, steps, for_each)

    async def tv_navigate(self, to, select=False, within=None, *, exact=False):
        """Move focus to an accessible label and optionally select it."""
        previous_value = None
        async def observe_target():
            nonlocal previous_value
            focus, items = await self.observe()
            if matches_label(focus, to, exact):
                previous_value = focus.get("value")
            return focus, items
        path = await navigate(observe_target, self.press, to, select, within, exact=exact)
        screen = await self.snapshot()
        # The first snapshot during a menu transition can still describe the
        # old page or have no focused row. Only repeat the observation.
        if select:
            for _ in range(2):
                if screen["focus"] and (not matches_label(screen["focus"], to, exact) or (previous_value is not None and screen["focus"].get("value") not in (None, previous_value))):
                    break
                await asyncio.sleep(0.3)
                screen = await self.snapshot()
        moved = not matches_label(screen["focus"], to, exact)
        value_changed = (not moved and previous_value is not None and screen["focus"] and
                         screen["focus"].get("value") not in (None, previous_value))
        verified = bool(screen["focus"] and (moved or value_changed)) if select else not moved
        evidence = ["Focused label matched"]
        if select:
            evidence.append(
                "Accessibility value changed" if value_changed else "Selection changed focus" if verified else "Selection acknowledged; effect unconfirmed"
            )
        return outcome(
            f"{'Selected' if select else 'Focused'} {to}.", evidence, {"path": path}, verified
        ) | {"screen": screen}

    async def open_app(self, app, *, require_verified_launch=False):
        match = match_app(app, (await self.ops.tv_list_apps())["apps"])
        result = await self.ops.tv_open(app=match["bundle_id"])
        if require_verified_launch:
            checked(result)
        if result["ok"] or result["error"]["code"] == "open_unverified":
            await self.resolve_prompts()
        if not result["ok"] and result["error"]["code"] == "open_unverified":
            snapshot = await self.snapshot()
            if (snapshot["app"] or {}).get("id") == match["bundle_id"]:
                return snapshot["app"]
        checked(result)
        return {"id": match["bundle_id"], "name": match["name"]}

    async def search(self, match, query):
        recipe = RECIPES["default"] | RECIPES.get(match["name"], {})
        if template := recipe.get("search_link"):
            async with self.engine.session() as client:
                await self.ops.launch(client, template.format(query=quote(query, safe="")))
            return "search_link"
        await navigate(self.observe, self.press, recipe["search_button"], True)
        checked(await self.ops.tv_type(query))
        return "in_app_search"

    async def tv_open(self, app, search=None, path=None, *, require_verified_launch=False):
        """Open an installed app by name or bundle ID, then search or follow a menu path."""
        await self.takeover()
        match = await self.open_app(app, require_verified_launch=require_verified_launch)
        data = {"app": match}
        verified = True
        evidence = ["Foreground app matched"]
        if search:
            data["path"] = await self.search(match, search)
            if data["path"] == "in_app_search":
                evidence.append("Keyboard text matched the query")
            else:
                try:
                    focus, items = await self.observe()
                    verified = any(
                        normal(search) in normal(label(item)) for item in [focus, *items]
                    )
                except Fault:
                    verified = False
                evidence.append(
                    "Search text observed"
                    if verified
                    else "Search link opened; results unconfirmed"
                )
        elif path:
            if match["id"] == "com.apple.TVSettings":
                await self.settings_root()
            data.update(path=[], steps=[])
            for index, part in enumerate(path):
                action = await self.tv_navigate(part, select=True)
                data["path"].extend(action["data"]["path"])
                evidence.extend(action["evidence"])
                destination = action["screen"]
                step = {
                    "target": part,
                    "verified": action["verified"],
                    "focus_after": label(destination["focus"]) or None,
                }
                data["steps"].append(step)
                if step["verified"]:
                    observation = await self.read_observation()
                    focus = observation["focused"]
                    destination = await self.snapshot(focus, False, observation)
                    labels = [normal(label(item)) for item in observation["items"]]
                    step["focus_after"] = label(focus) or None
                    step["target_visible"] = normal(part) in [normal(label(focus)), *labels]
                    arrived = step["target_visible"] if index == len(path) - 1 else (
                        focus and normal(label(focus)) in labels
                    )
                    step["verified"] = bool(arrived and (destination["app"] or {}).get("id") == match["id"])
                if not step["verified"]:
                    raise Fault(
                        "uncertain",
                        f"Selected {part}, but the destination could not be verified.",
                        "Read tv_look before continuing the menu path.",
                        evidence=evidence,
                        **data,
                    )
        result = outcome(f"Opened {match['name']}.", evidence, data, verified=verified)
        return result | {"screen": destination} if path else result

    async def wait_playing(self, before, expected=None, title=None, airplay=False):
        deadline = asyncio.get_running_loop().time() + self.play_timeout
        while True:
            s = await self.engine.status()
            playing = s.get("now_playing") or {}
            matches_app = not expected or (s.get("app") or {}).get("bundle_id") == expected
            observed = (
                s.get("airplay_playing")
                if airplay
                else playback_identity(playing) != playback_identity(before)
                or (title and normal(title) == normal(playing.get("title")))
            )
            if (
                matches_app
                and playing.get("state") == "playing"
                and observed
                and (not title or normal(title) in normal(playing.get("title")))
            ):
                return playing
            if self.media.problem:
                raise self.media.problem
            if asyncio.get_running_loop().time() >= deadline:
                raise Fault(
                    "uncertain",
                    "The requested playback could not be verified.",
                    "Read tv_look before trying again.",
                )
            await asyncio.sleep(0.25)

    async def play_link(self, link, before):
        url, rule = link_info(link)
        targets = link_targets(url, rule)

        async def launch():
            try:
                async with self.engine.session() as client:
                    await self.ops.launch(client, url)
            except Fault as exc:
                if exc.code not in ("command_rejected", "launch_refused"):
                    raise
                raise Fault(
                    "launch_refused",
                    str(exc),
                    "For this URL, try another link form or tv_open + tv_navigate.",
                ) from exc

            if urlsplit(url).scheme not in ("http", "https"):
                await confirm_custom_link(self, targets)

        await launch()
        if await self.resolve_prompts():
            await launch()
            await self.resolve_prompts()

        deadline = asyncio.get_running_loop().time() + self.play_timeout
        while True:
            view = await observed(self)
            status = await self.engine.status()
            screen = await self.snapshot(
                view.get("focused"), False, view, status=status
            )
            app = screen["app"] or {}
            media = status.get("now_playing") or {}
            owner = (status.get("app") or {}).get("bundle_id")
            fresh_playback = media.get("state") == "playing" and playback_identity(
                media
            ) != playback_identity(before)
            foreground = app.get("id") if app.get("source") == "accessibility" else None
            # Without AX, changed playback from the expected app is independent
            # evidence. Paused or unchanged now-playing metadata can outlive the UI.
            if (
                foreground is None
                and fresh_playback
                and not (view.get("app") or view.get("focused"))
            ):
                foreground = owner
            if foreground in targets:
                evidence = [f"Observed target app {targets[foreground]}"]
                labels = {normal(label(i)) for i in view.get("items", [])}
                player_controls = bool(
                    "pause" in labels
                    and labels & {"elapsed time", "remaining time", "scrubber"}
                )
                matching_content = media.get("content_identifier") in (link, url)
                if (
                    owner == foreground
                    and media.get("state") == "playing"
                    and (fresh_playback or matching_content or player_controls)
                ):
                    landed = "playing"
                    evidence.append("Playback verified in the target app")
                elif action := title_action(view):
                    landed = "title_page"
                    evidence.append(f"Title action: {action}")
                else:
                    landed = "app_home"
                    if focused := label(view.get("focused")):
                        evidence.append(f"Focused: {focused}")
                summary = {
                    "playing": "Playback started",
                    "title_page": "Opened the title page",
                    "app_home": "Opened the app",
                }[landed]
                return outcome(
                    f"{summary} in {targets[foreground]}.",
                    evidence,
                    {"path": "deep_link", "landed": landed},
                ) | {"screen": screen}
            if asyncio.get_running_loop().time() >= deadline or not targets:
                raise Fault(
                    "uncertain",
                    "Could not verify the link’s target app in the foreground.",
                    "Read tv_look before continuing.",
                )
            await asyncio.sleep(0.25)

    async def tv_play(self, youtube=None, media=None, link=None, title=None, app=None):
        """Play a video or title, or open a deep link and report where it landed."""
        await self.takeover()
        before = (await self.engine.status()).get("now_playing")
        expected = None
        if youtube:
            async def after_youtube_launch():
                # youtube:// links can raise tvOS's "Open in YouTube?" dialog
                # (seen on tvOS 26.6.1); accessibility text cannot see it.
                await confirm_custom_link(self, {"com.google.ios.youtube": "YouTube"})
                return await self.resolve_prompts()

            result = checked(await self.ops.tv_play_youtube(url=youtube, timeout=self.play_timeout / 2, after_launch=after_youtube_launch))
            return outcome("YouTube playback was observed.", result["evidence"], {"path": "youtube"})
        if media:
            if urlsplit(media).scheme not in ("http", "https") or not urlsplit(media).hostname:
                raise Fault(
                    "invalid_arguments",
                    "Media must be an HTTP or HTTPS URL.",
                    "Supply a receiver-accessible media URL.",
                )
            await self.media.start(media)
            route = "airplay"
        elif link:
            return await self.play_link(link, before)
        else:
            match = await self.open_app(app)
            expected = match["id"]
            recipe = RECIPES["default"] | RECIPES.get(match["name"], {})
            known = recipe.get("titles", {}).get(normal(title))
            if known:
                async with self.engine.session() as client:
                    await self.ops.launch(client, recipe["title_link"].format(id=quote(known, safe="/.")))
                if await self.resolve_prompts():
                    async with self.engine.session() as client:
                        await self.ops.launch(client, recipe["title_link"].format(id=quote(known, safe="/.")))
                    await self.resolve_prompts()
                route = "title_link"
            else:
                route = await self.search(match, title)
                await navigate(self.observe, self.press, title, True)
                current = (await self.engine.status()).get("now_playing") or {}
                if current.get("state") != "playing" or normal(title) not in normal(
                    current.get("title")
                ):
                    _, items = await self.observe()
                    for button in recipe["play_buttons"]:
                        if any(normal(label(i)).startswith(normal(button)) for i in items):
                            await navigate(self.observe, self.press, button, True)
                            break
        playing = await self.wait_playing(before, expected, title, airplay=bool(media))
        return outcome(
            f"Playing {playing.get('title') or title or 'the requested media'}.",
            ["Playback state: playing"],
            {"path": route},
        )

    async def tv_hide(self):
        """Stop the current presentation (compatibility alias)."""
        return await self.tv_show()

    async def tv_show(
        self, url=None, html=None, files=None, bundle=None, title=None, audio="play", keep_awake=True
    ):
        """Show a live URL, HTML, inline files or an uploaded bundle. Use {} to hide."""
        await self.media.stop()
        if all(source is None for source in (url, html, files, bundle)):
            await self.ops.tv_hide()
            return outcome("Stopped presenting.", ["Sender stopped"])
        result = await self.ops.present(
            url=url,
            html=html,
            files=files,
            bundle=bundle,
            title=title,
            audio=audio,
            keep_awake=keep_awake,
        )
        mode = result["mode"]
        return outcome(
            "The bridge is presenting the page."
            if result["verified"]
            else "Page delivery is awaiting confirmation.",
            result["evidence"],
            {
                "page_id": result["page_id"],
                "size": self.size if mode == "mirror" else "1920x1080",
                "transport": "mirror" if mode == "mirror" else "video",
            },
            result["verified"],
        )

    async def tv_update(self, data):
        """Deliver JSON to the current page and read its latest state."""
        await self.ops.update_page(data)
        return outcome(
            "Delivered the page update.",
            ["Browser posted the update"],
            {"delivered": True, "page_state": await self.stage.current_state()},
        )
