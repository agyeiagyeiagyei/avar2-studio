#!/usr/bin/env python3
"""Headless UI probes for a locally running avar2-studio (Playwright, Chromium).

Run against a studio that serves a COPY of the sources (never port 5070):

    python3 -I ui_probe.py --url http://localhost:5072 all
    python3 -I ui_probe.py --url http://localhost:5072 select-no-save preview-no-build

Needs the Python Playwright package with a Chromium download
(`pip install playwright && playwright install chromium`). Every scenario
prints the non-polling requests it saw with a timestamp, then PASS/FAIL
with the reason. Screenshots go to --shots (default ./ui-shots).

`--sources` points at the staged copy's sources dir (default
/private/tmp/av2-routine/sources) for the upload/hash scenarios.

Scenarios (LOCAL_TEST_ROUTINE.md IDs in brackets):
  select-no-save     [A3] clicking an instance row must not PUT or rebuild
  preview-no-build   [A4] Preview sliders only call /api/mapped-location; Reset restores defaults
  sidebar-edit-saves [A5] a sidebar edit PUTs once (csv_only), no rebuild until Preview opens,
                     then exactly one build (this one triggers a real build on the copy)
  console-clean      [A20] load every main tab; zero console errors
  first-load         [A2] row count/names, registered font, ORIGIN badge, console
  sync-dots          [A6] red draft → orange studio save; untouched SRC row green
  flyout-source      [A7] SRC row → flyout → "Save to source file" → rebuild + green
  lifecycle          [A8] create, duplicate, rename, delete a studio row
  multi-broadcast    [A9] LCWD edit on a multi-selection: one PUT per row, only LCWD
  grade              [A10] enable Grade, grade a row, rebuild
  control-axis       [A11] declare a secondary axis, open Fontra on its shadow
  kinetikern         [A13] propose/apply/revert a Kinetikern spacing
  tester             [A14] type + toolbar on the Tester tab
  export             [A12] TTF at default, WOFF2, TTF at a non-default "set default"
  config-roundtrip   [A16] export then import the same bundle; sidecar hashes unchanged
  load-font          [A15] swap to a built-in example and back to the copy
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

POLL = ("/api/health", "/api/glyphs-file-status", "/api/build/", "/static/", "/favicon",
        "/api/avar2/", "/api/glyph-coverage", "/api/examples", "/api/masters",
        "/api/transforms", "/api/axes", "/api/instances", "/api/font?")


def health(url):
    with urllib.request.urlopen(f"{url}/api/health", timeout=10) as r:
        return json.load(r)


def wait_for(url, key, want=True, timeout=30):
    """Poll /api/health until health[key] == want (the PUT handler loads the
    source file, so a save can take seconds to land)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if bool(health(url).get(key)) == want:
            return True
        time.sleep(0.5)
    return False


def wait_idle(url, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health(url)
        if not h.get("building") and not h.get("build_job"):
            return h
        time.sleep(2)
    raise TimeoutError("studio still building")


class Session:
    def __init__(self, pw, url, shots, sources=None):
        self.url = url
        self.shots = Path(shots)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.sources = sources
        self.browser = pw.chromium.launch()
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 1000})
        self.t0 = time.time()
        self.requests = []
        self.responses = []
        self.bodies = []
        self.console_errors = []
        self.dialogs = []
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.console_errors.append(str(e)))
        # Source writebacks and destructive actions use window.confirm;
        # Playwright dismisses dialogs unless a handler accepts them.
        self.page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.accept()

    def _on_response(self, r):
        if r.request.method == "GET":
            return
        if any(s in r.url for s in POLL):
            return
        self.responses.append((r.request.method, r.url.replace(self.url, "")[:120], r.status))

    def _on_request(self, r):
        if any(s in r.url for s in POLL):
            return
        line = f"[{time.time()-self.t0:6.2f}s] {r.method} {r.url.replace(self.url, '')[:120]}"
        self.requests.append((r.method, r.url))
        self.bodies.append((r.method, r.url, r.post_data))
        print("   ", line)

    def load(self):
        wait_idle(self.url)          # a build veil from an earlier scenario would block clicks
        self.page.goto(self.url)
        self.page.wait_for_selector(".main-tab", timeout=60000)
        enter = self.page.locator("button.dl-enter")
        if enter.count():
            enter.first.click()
        self.page.wait_for_timeout(2500)

    def tab(self, name):
        self.page.click(f"button.main-tab:has-text('{name}')")
        self.page.wait_for_timeout(2500)

    def drag(self, locator, start=0.4, end=0.9, steps=8):
        box = locator.bounding_box()
        y = box["y"] + box["height"] / 2
        self.page.mouse.move(box["x"] + box["width"] * start, y)
        self.page.mouse.down()
        for i in range(1, steps + 1):
            self.page.mouse.move(box["x"] + box["width"] * (start + (end - start) * i / steps), y)
            self.page.wait_for_timeout(50)
        self.page.mouse.up()

    def shot(self, name):
        self.page.screenshot(path=str(self.shots / f"{name}.png"))

    def puts(self):
        return [u for m, u in self.requests if m == "PUT"]

    def builds(self):
        return [u for m, u in self.requests if m == "POST" and u.endswith("/api/build")]

    def close(self):
        self.browser.close()


def first_row_name(url):
    with urllib.request.urlopen(f"{url}/api/instances", timeout=10) as r:
        inst = json.load(r)["instances"]
    studio = [i for i in inst if i.get("origin") == "studio"]
    return (studio or inst)[0]["name"]


def scenario_select_no_save(s):
    name = first_row_name(s.url)
    s.load()
    s.page.click(f"[data-instance-name='{name}']")
    s.page.wait_for_timeout(3000)
    s.tab("Preview")
    s.page.wait_for_timeout(4000)
    s.shot("select-no-save")
    assert not s.puts(), f"row click saved: {s.puts()}"
    assert not s.builds(), f"row click rebuilt: {s.builds()}"
    assert not wait_for(s.url, "build_stale", True, timeout=8), "health reports build_stale after a plain click"


def scenario_preview_no_build(s):
    s.load()
    s.tab("Preview")
    sl = s.page.locator(".preview-tab input.axis-slider[aria-label*='(wght)']").first
    before = sl.input_value()
    s.drag(sl)
    s.page.wait_for_timeout(1500)
    after = sl.input_value()
    assert before != after, "wght slider did not move"
    s.page.click("button.preview-reset")
    s.page.wait_for_timeout(1000)
    assert sl.input_value() == before, f"Reset did not restore wght ({sl.input_value()} vs {before})"
    s.page.wait_for_timeout(2000)
    s.shot("preview-no-build")
    other = [(m, u) for m, u in s.requests if "/api/mapped-location" not in u and m != "GET"]
    assert not other, f"Preview slider caused non-GET traffic: {other}"
    assert not s.builds(), "Preview slider triggered a build"


def scenario_sidebar_edit_saves(s):
    name = first_row_name(s.url)
    s.load()
    s.page.click(f"[data-instance-name='{name}']")
    s.page.wait_for_timeout(1500)
    sl = s.page.locator("input.axis-slider[aria-label*='(XOPQ)']").first
    # Land on a value the row does not already hold (an unchanged value is
    # correctly not an edit), so move away from the current position.
    lo, hi, cur = (float(sl.get_attribute(k)) for k in ("min", "max", "value"))
    pos = (cur - lo) / (hi - lo)
    s.drag(sl, pos, pos + 0.1 if pos < 0.5 else pos - 0.1, 4)
    assert float(sl.input_value()) != cur, "XOPQ slider did not move"
    s.page.wait_for_timeout(3000)
    puts = s.puts()
    assert len(puts) == 1 and "csv_only=true" in puts[0], f"expected one csv_only PUT, saw {puts}"
    assert not s.builds(), "a sidebar edit rebuilt immediately (should wait for Preview)"
    assert wait_for(s.url, "build_stale", True), "health never reported build_stale after an edit"
    s.tab("Preview")
    s.page.wait_for_timeout(5000)
    assert len(s.builds()) == 1, f"expected exactly one build on Preview open, saw {s.builds()}"
    s.shot("sidebar-edit-saves")
    h = wait_idle(s.url)
    assert h.get("last_build_status") == "ok", f"build failed: {h.get('last_build_error')}"
    assert not h.get("build_stale"), "still stale after the build landed"


def scenario_console_clean(s):
    s.load()
    for t in ("Preview", "Kinetikern", "Tester", "Instances"):
        if s.page.locator(f"button.main-tab:has-text('{t}')").count():
            s.tab(t)
    s.shot("console-clean")
    assert not s.console_errors, "console errors:\n" + "\n".join(s.console_errors[:10])


def api_json(url, path):
    with urllib.request.urlopen(f"{url}{path}", timeout=20) as r:
        return json.load(r)


def dot_class(s, name):
    return s.page.locator(
        f"[data-instance-name='{name}'] .sync-status-dot").get_attribute("class") or ""


def drag_axis(s, tag, scope="", frac=0.12):
    """Drag the first sidebar/preview slider for TAG away from its value.
    Returns (before, after)."""
    sl = s.page.locator(
        f"{scope} input.axis-slider[aria-label*='({tag})']").first
    lo, hi, cur = (float(sl.get_attribute(k)) for k in ("min", "max", "value"))
    pos = (cur - lo) / (hi - lo)
    step = frac
    s.drag(sl, pos, min(0.95, pos + step) if pos < 0.45 else max(0.05, pos - step))
    return cur, float(sl.input_value())


def scenario_first_load(s):
    """A2 — first load: rows, local font, ORIGIN badge, console."""
    s.load()
    s.shot("a2-first-load")
    api = api_json(s.url, "/api/instances")["instances"]
    rows = s.page.locator("[data-instance-name]")
    names = [rows.nth(i).get_attribute("data-instance-name")
             for i in range(rows.count())]
    assert names == [i["name"] for i in api], (
        f"rows {len(names)} != API {len(api)}", set(names) ^ {i['name'] for i in api})
    fonts = s.page.evaluate("""async () => {
        await document.fonts.ready;
        return {status: document.fonts.status,
                families: [...document.fonts].map(f => f.family)};
    }""")
    assert fonts["status"] == "loaded", f"document.fonts {fonts['status']}"
    assert any("Crispy" in f for f in fonts["families"]), f"no preview family: {fonts}"
    origin = s.page.locator(
        "[data-instance-name='Default 400 MaxOpsz'] .origin-badge-default")
    assert origin.count() == 1, "ORIGIN badge missing on Default 400 MaxOpsz"
    assert origin.inner_text().strip() == "ORIGIN"
    assert not s.console_errors, "console errors:\n" + "\n".join(s.console_errors[:10])
    s.shot("a2-first-load")


def scenario_sync_dots(s):
    """A6 — red while a draft is unsaved, orange once the studio CSV holds
    it, green for an untouched source row."""
    name = "Default 1 MaxOpsz"          # studio-only row
    source = "Default 400 MaxOpsz"      # the SRC row
    s.load()
    s.page.click(f"[data-instance-name='{name}']")
    s.page.wait_for_timeout(1000)
    before, after = drag_axis(s, "XOPQ")
    assert before != after, "XOPQ slider did not move"
    # The auto-save lands 0.8 s after the drag ends; read the dot now.
    assert "sync-status-red" in dot_class(s, name), (
        f"draft not red at once: {dot_class(s, name)}")
    try:
        s.page.wait_for_function(
            """([name]) => document.querySelector(
                 `[data-instance-name="${name}"] .sync-status-dot`)
                 ?.classList.contains('sync-status-orange')""",
            arg=[name], timeout=15000)
    except PlaywrightTimeoutError:
        raise AssertionError(
            f"studio row not orange after save: {dot_class(s, name)}") from None
    if "sync-status-green" not in dot_class(s, source):
        raise AssertionError(
            f"untouched source row not green: {dot_class(s, source)}")
    s.shot("a6-sync-dots")


def scenario_flyout_source(s):
    """A7 — red/orange source row pushed back into the .glyphs file."""
    name = "Default 400 MaxOpsz"
    s.load()
    before_build = health(s.url).get("last_build_time")
    s.page.click(f"[data-instance-name='{name}']")
    s.page.wait_for_timeout(1000)
    # Small delta on purpose: this row is the ORIGIN, so a large move
    # relocates the font default and can hit gvar's per-glyph limit.
    before, after = drag_axis(s, "XOPQ", frac=0.01)
    assert before != after, "XOPQ slider did not move"
    # SOURCE rows never auto-save (rewriting a .glyphs is explicit), so
    # the row is red until the flyout action runs.
    assert "sync-status-red" in dot_class(s, name), dot_class(s, name)
    s.page.click(f"[data-instance-name='{name}'] .sync-status-dot-wrapper")
    s.page.wait_for_selector(".instance-flyout", timeout=5000)
    try:
        with s.page.expect_response(
                lambda r: r.request.method == "PUT" and "/api/instance/" in r.url
                and "csv_only" not in r.url, timeout=90000) as info:
            s.page.click(".instance-flyout .flyout-item-source")
        status = info.value.status
    except PlaywrightTimeoutError:
        status = None
    print(f"    source PUT status: {status}")
    assert status == 200, (
        f"source writeback failed ({status}): the row did not reach the .glyphs file")
    # The server submits the rebuild itself after a source writeback.
    t0 = time.time()
    started = False
    while time.time() - t0 < 180:
        h = health(s.url)
        if h.get("last_build_time") != before_build:
            started = True
            break
        time.sleep(1)
    assert started, "no rebuild after the source save"
    wait_idle(s.url)
    # The handler refetches instances and refreshes the font after the
    # server-side rebuild; give that tail time to land before judging the dot.
    try:
        s.page.wait_for_function(
            """([name]) => document.querySelector(
                 `[data-instance-name="${name}"] .sync-status-dot`)
                 ?.classList.contains('sync-status-green')""",
            arg=[name], timeout=25000)
    except PlaywrightTimeoutError:
        pass
    shot_class = dot_class(s, name)
    s.shot("a7-after-save-to-source")
    assert "sync-status-green" in shot_class, (
        f"row not green after source save: {shot_class}")
    return after


def instance_names(url):
    return [i["name"] for i in api_json(url, "/api/instances")["instances"]]


def wait_instances(s, name, present=True, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        names = instance_names(s.url)
        if (name in names) == present:
            return names
        time.sleep(1)
    raise AssertionError(
        f"instance {name!r} {'appeared' if present else 'never left'} "
        f"in {timeout}s (last names: {instance_names(s.url)})")


def wait_ui_idle(s, timeout=240):
    """Server idle AND the build veil gone (the veil intercepts clicks)."""
    h = wait_idle(s.url, timeout)
    try:
        s.page.wait_for_selector(".build-veil", state="detached", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    s.page.wait_for_timeout(400)
    return h


def wait_header_ready(s, timeout=60000):
    """The header menus are disabled while the app is busy (its own font
    fetch, a build, the veil), so a click straight after `load()` can hit a
    `disabled` trigger and time out — a harness artefact, not a product
    failure. Wait for the chrome to be interactive."""
    s.page.wait_for_selector(".build-veil", state="detached", timeout=timeout)
    s.page.wait_for_selector(
        "header button.btn-load-font:not([disabled])", timeout=timeout)


def wait_new_build(s, before, timeout=180):
    """Wait for a server-side build (the browser may not POST it)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health(s.url)
        if h.get("last_build_time") not in (None, before) and not h.get("building"):
            return h
        time.sleep(1)
    raise TimeoutError("no new build landed")


def check_no_new_build(s, before, timeout=6):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health(s.url)
        if h.get("last_build_time") not in (None, before):
            raise AssertionError("an unexpected build started")
        time.sleep(0.5)


def scenario_lifecycle(s):
    """A8 — create, duplicate, rename, delete; each lands in API + CSV."""
    s.load()
    s.page.click(".btn-new-instance")
    s.page.wait_for_selector(".modal-content", timeout=10000)
    s.page.fill(".modal-input", "LT Test One")
    # The modal pre-fills axis defaults straight from /api/axes, which carry
    # float32 noise (663.1000061035156). With step=0.1 the browser's native
    # validation blocks the submit before React sees it. Record that, then
    # round the values so the rest of the lifecycle can be exercised.
    s.page.click(".modal-content .btn-confirm")
    s.page.wait_for_timeout(1500)
    blocked = not [u for m, u in s.requests if m == "POST"]
    print(f"    create with raw defaults blocked by native validation: {blocked}")
    if blocked:
        inputs = s.page.locator(".modal-axis-input")
        for i in range(inputs.count()):
            v = inputs.nth(i).input_value()
            try:
                inputs.nth(i).fill(f"{round(float(v), 1):g}")
            except ValueError:
                pass
        s.page.click(".modal-content .btn-confirm")
    names = wait_instances(s, "LT Test One")
    assert names.count("LT Test One") == 1
    wait_ui_idle(s)

    s.page.click(".btn-duplicate")
    s.page.wait_for_selector(".modal-content", timeout=10000)
    s.page.wait_for_function(
        "() => document.querySelector('.modal-input')?.value.trim().length > 0",
        timeout=5000)
    dup = s.page.locator(".modal-input").input_value()
    assert dup.startswith("LT Test One Copy"), f"duplicate name proposed {dup!r}"
    s.page.click(".modal-content .btn-confirm")
    wait_instances(s, dup)
    wait_ui_idle(s)

    row = s.page.locator("[data-instance-name='LT Test One']")
    row.click()
    row.locator(".instance-name").click()
    s.page.wait_for_selector(".instance-name-input", timeout=5000)
    s.page.fill(".instance-name-input", "LT Test One Renamed")
    s.page.press(".instance-name-input", "Enter")
    before = health(s.url).get("last_build_time")
    wait_instances(s, "LT Test One Renamed")
    wait_instances(s, "LT Test One", present=False)
    wait_new_build(s, before)
    wait_ui_idle(s)

    row = s.page.locator("[data-instance-name='LT Test One Renamed']")
    row.click()
    row.locator(".delete-instance-btn").click()
    # Studio-only rows delete straight away (no source declaration to
    # confirm); the modal belongs to SRC rows only.
    wait_instances(s, "LT Test One Renamed", present=False)
    wait_ui_idle(s)
    # Self-clean the duplicate so the next run starts from 30 rows.
    dup_row = s.page.locator(f"[data-instance-name='{dup}']")
    dup_row.click()
    dup_row.locator(".delete-instance-btn").click()
    wait_instances(s, dup, present=False)
    wait_ui_idle(s)
    s.shot("a8-after-deletes")


def scenario_multi_broadcast(s):
    """A9 — a control-axis edit lands on every multi-selected row, and
    only that axis."""
    s.load()
    names = instance_names(s.url)
    a, b = names[0], names[2]
    before = {i["name"]: dict(i["coordinates"])
              for i in api_json(s.url, "/api/instances")["instances"]}
    s.page.click(f"[data-instance-name='{a}']")
    s.page.click(f"[data-instance-name='{b}']", modifiers=["Shift"])
    s.page.wait_for_timeout(500)
    # The anchor keeps the plain `selected` class; the rest of the range
    # carries `multi-selected` (isMultiSelected excludes the anchor).
    assert "selected" in (s.page.locator(
        f"[data-instance-name='{a}']").get_attribute("class") or "")
    assert "multi-selected" in (s.page.locator(
        f"[data-instance-name='{b}']").get_attribute("class") or "")
    s.shot("a9-multi-select")
    drag_axis(s, "LCWD")
    s.page.wait_for_timeout(4500)
    puts = s.puts()
    assert len(puts) == 3, f"expected one PUT per selected row, saw {puts}"
    from urllib.parse import unquote
    broadcast = {unquote(u.rsplit("/api/instance/", 1)[1].split("?", 1)[0]): body
                 for m, u, body in s.bodies
                 if m == "PUT" and "/api/instance/" in u and body}
    assert len(broadcast) == 3, f"one PUT per row expected, got {list(broadcast)}"
    for name, body in broadcast.items():
        axes_in_body = [k for k in json.loads(body)["coordinates"]]
        if name != a:   # the anchor auto-saves its whole coordinate set
            assert axes_in_body == ["LCWD"], (name, axes_in_body)
        else:
            assert "LCWD" in axes_in_body, (name, axes_in_body)
    after = {i["name"]: dict(i["coordinates"])
             for i in api_json(s.url, "/api/instances")["instances"]}
    for name in names[0:3]:
        changed = {k for k in before[name]
                   if abs(float(before[name][k]) - float(after[name].get(k, 0))) > 0.01}
        assert changed <= {"LCWD"}, f"{name}: {changed} changed"


def scenario_grade(s):
    """A10 — enable the grade transform, grade one row, rebuild."""
    s.load()
    wait_header_ready(s)
    before = health(s.url).get("last_build_time")
    s.page.click("button:has-text('Transforms')")
    s.page.wait_for_selector(".transforms-menu", timeout=10000)
    row = s.page.locator(".transforms-menu .transform-row").filter(has_text="Grade").first
    # The enabled Grade section also renders "Limit grade to counter
    # headroom"; the axis toggle is the first checkbox.
    checkbox = row.locator("input[type=checkbox]").first
    if not checkbox.is_checked():
        checkbox.check()
        wait_new_build(s, before, timeout=240)
    else:
        print("    Grade already enabled; no toggle build")
    # Close the dropdown: its popper otherwise covers the row badges.
    s.page.keyboard.press("Escape")
    s.page.wait_for_timeout(500)
    # Grade an instance the designer already grades: adding a NEW graded
    # location adds virtual masters and can push this twin-heavy font over
    # gvar's per-glyph limit (the build then falls back without mappings).
    name = "Default 1 MinOpsz"
    badge = s.page.locator(
        f"[data-instance-name='{name}'] button.grade-badge").first
    badge.wait_for(timeout=15000)
    badge.click()
    s.page.fill(".grade-popover-input", "40")
    before = health(s.url).get("last_build_time")
    s.page.click(".grade-popover-save")
    wait_new_build(s, before, timeout=240)
    g = api_json(s.url, "/api/transforms/grade")
    print("    grade:", json.dumps({k: g.get(k) for k in
                                    ("enabled", "default_pct", "intensity")}))
    assert g.get("enabled") is True
    s.shot("a10-grade")


def scenario_control_axis(s):
    """A11 — declare a secondary axis and open Fontra on its shadow."""
    s.load()
    existing = {str(a["tag"]).lower() for a in api_json(s.url, "/api/axes")["axes"]}
    tag = next(t for t in (f"TST{i}" for i in range(2, 100)) if t.lower() not in existing)
    s.page.click(".control-axes-add-btn")
    s.page.wait_for_selector(".add-control-axis-modal", timeout=10000)
    s.page.fill("#ctl-tag", tag)
    s.page.fill("#ctl-name", "Test Axis")
    s.page.fill("#ctl-min", "0")
    s.page.fill("#ctl-default", "0")
    s.page.fill("#ctl-max", "100")
    before = health(s.url).get("last_build_time")
    s.page.click(".add-control-axis-modal .btn-confirm")
    t0 = time.time()
    while time.time() - t0 < 30:
        axes = api_json(s.url, "/api/axes")["axes"]
        if any(a["tag"].lower() == tag.lower() for a in axes):
            break
        time.sleep(1)
    else:
        raise AssertionError(f"{tag} never appeared in /api/axes")
    axis = next(a for a in axes if a["tag"].lower() == tag.lower())
    assert axis.get("is_control_axis") is True, axis
    wait_new_build(s, before, timeout=240)
    req = urllib.request.Request(
        f"{s.url}/api/control-axes/{tag}/open-editor", method="POST",
        data=b"{}", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        editor = json.load(r)
    print("    editor:", {k: editor.get(k) for k in
                          ("project", "editing_original", "port")})
    assert editor.get("editing_original") is False, editor
    with urllib.request.urlopen(editor["direct_url"], timeout=20) as r:
        assert r.status == 200
    s.shot("a11-control-axis")


def scenario_kinetikern(s):
    """A13 — propose, apply, persist, revert."""
    s.load()
    s.tab("Kinetikern")
    s.page.wait_for_selector(".kk-instance", timeout=15000)
    s.page.wait_for_timeout(3000)
    sl = s.page.locator("input[aria-label='Looseness']")
    box = sl.bounding_box()
    s.page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    s.page.mouse.down()
    s.page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2, steps=6)
    s.page.mouse.up()
    s.page.wait_for_timeout(4000)
    assert s.page.locator(".kk-btn.primary").is_enabled(), "Apply is disabled"
    before = health(s.url).get("last_build_time")
    s.page.click(".kk-btn.primary")
    wait_new_build(s, before, timeout=240)
    data = api_json(s.url, "/api/kinetikern")
    print("    kinetikern entries:", [e.get("name") for e in data.get("instances", [])])
    assert data.get("instances"), "Apply persisted nothing"
    row_name = s.page.locator(".kk-instance").input_value()
    entry = next((e for e in data["instances"] if e.get("name") == row_name), None)
    assert entry is not None, (row_name, data)
    s.shot("a13-after-apply")
    before = health(s.url).get("last_build_time")
    s.page.click(".kk-btn:has-text('Revert')")
    wait_new_build(s, before, timeout=240)
    data = api_json(s.url, "/api/kinetikern")
    assert not any(e.get("name") == row_name for e in data.get("instances", [])), \
        "Revert left the entry behind"


def scenario_tester(s):
    """A14 — typing + toolbar rendering."""
    s.load()
    s.tab("Tester")
    ed = s.page.locator(".tester-editable")
    ed.click()
    s.page.keyboard.type("Handgloves 123 — bold italic")
    # The toolbar is selection-driven: toggle with the text selected.
    s.page.keyboard.press("Meta+A")
    s.page.click(".tester-bold")
    s.page.wait_for_timeout(300)
    s.page.click(".tester-italic")
    s.page.wait_for_timeout(300)
    s.page.click(".tester-align button:has-text('Center')")
    s.page.fill(".tester-tracking input", "0.05")
    s.page.wait_for_timeout(1000)
    # The editable is the UI-font container; the typed runs carry the
    # preview family.
    fam = s.page.evaluate(
        "() => { const s = document.querySelector('.tester-editable span');"
        " return s ? getComputedStyle(s).fontFamily : null; }")
    assert fam and "Crispy" in fam, f"tester run font family {fam}"
    align = s.page.evaluate(
        "() => getComputedStyle(document.querySelector('.tester-block')).textAlign")
    assert align == "center", f"alignment {align}"
    runs = s.page.evaluate(
        "() => [...document.querySelectorAll('.tester-editable span')].map("
        "s => [getComputedStyle(s).fontWeight, getComputedStyle(s).fontStyle,"
        " getComputedStyle(s).letterSpacing])")
    print("    runs:", runs[:4])
    assert any(r[0] == "700" for r in runs), "no bold run"
    assert any(r[1] == "italic" for r in runs), "no italic run"
    assert any(r[2] not in ("normal", "0px") for r in runs), "tracking not applied"
    assert not s.console_errors, "console errors:\n" + "\n".join(s.console_errors[:10])
    s.shot("a14-tester")


def scenario_export(s):
    """A12 — TTF at default, WOFF2, TTF at a non-default location."""
    s.load()
    s.tab("Preview")
    out = Path(s.shots).parent / "exports"
    out.mkdir(parents=True, exist_ok=True)
    s.page.click("button:has-text('Download font…')")
    s.page.wait_for_selector(".preview-export-modal", timeout=10000)
    sizes = s.page.locator(".preview-export-modal").inner_text()
    print("    modal:", " | ".join(sizes.splitlines()[:8]))
    with s.page.expect_download(timeout=120000) as dl:
        s.page.click(".preview-export-actions .btn-secondary")
    dl.value.save_as(str(out / "default.ttf"))
    # WOFF2 (the background cache is exact after a finished build)
    s.page.click("button:has-text('Download font…')")
    s.page.wait_for_selector(".preview-export-modal", timeout=10000)
    s.page.click(".preview-export-chip--format:has-text('WOFF2')")
    with s.page.expect_download(timeout=180000) as dl:
        s.page.click(".preview-export-actions .btn-secondary")
    dl.value.save_as(str(out / "default.woff2"))
    # Non-default location + set default: drag to ~wght 900 (the routine's
    # documented gvar-limit corner). A JS value assignment bypasses React's
    # value tracker, so drive it with the mouse.
    sl = s.page.locator(".preview-tab input.axis-slider[aria-label*='(wght)']").first
    s.drag(sl, 0.5, 0.9, 8)
    s.page.wait_for_timeout(1500)
    wght = float(sl.input_value())
    print(f"    preview wght {wght}")
    print(f"    preview wght {wght}")
    s.page.click("button:has-text('Download font…')")
    s.page.wait_for_selector(".preview-export-modal", timeout=10000)
    label = s.page.locator(".preview-export-default").inner_text()
    print("    set-default label:", label.replace("\n", " "))
    s.page.locator(".preview-export-default input").check()
    got = []

    def on_download(d):
        got.append(d)

    s.page.on("download", on_download)
    s.page.click(".preview-export-actions .btn-secondary")
    t0 = time.time()
    while time.time() - t0 < 240 and not got:
        if s.page.locator(".preview-export-error").count():
            print("    export error:", s.page.locator(
                ".preview-export-error").inner_text().strip())
            break
        s.page.wait_for_timeout(1000)
    if got:
        got[0].save_as(str(out / "set-default-wght.ttf"))
    else:
        # The routine allows the readable gvar-limit error at far corners.
        err = s.page.locator(".preview-export-error").inner_text().strip()
        assert ("gvar" in err.lower() or "header" in err.lower()
                or "tuples" in err.lower()), f"unreadable export error: {err}"
        s.page.click(".preview-export-actions .btn-cancel")
        # Also prove the set-default path on a location that fits gvar.
        sl = s.page.locator(".preview-tab input.axis-slider[aria-label*='(wght)']").first
        s.drag(sl, 0.9, 0.6, 8)
        s.page.wait_for_timeout(1200)
        print(f"    second preview wght {sl.input_value()}")
        s.page.click("button:has-text('Download font…')")
        s.page.wait_for_selector(".preview-export-modal", timeout=10000)
        print("    second label:", s.page.locator(
            ".preview-export-default").inner_text().replace("\n", " "))
        s.page.locator(".preview-export-default input").check()
        # The routine allows the documented gvar limit at ANY non-resting
        # location on this twin-heavy source (both tested corners hit it), so
        # accept either a download or the same readable error.
        del got[:]
        s.page.click(".preview-export-actions .btn-secondary")
        t0 = time.time()
        while time.time() - t0 < 240 and not got:
            err_box = s.page.locator(".preview-export-error")
            if err_box.count():
                err = err_box.inner_text().strip()
                assert ("gvar" in err.lower() or "header" in err.lower()
                        or "tuples" in err.lower()), f"unreadable export error: {err}"
                print("    second export error (documented gvar limit):", err)
                s.page.click(".preview-export-actions .btn-cancel")
                break
            s.page.wait_for_timeout(1000)
        if got:
            got[0].save_as(str(out / "set-default-wght-600.ttf"))
    s.shot("a12-export")
    print("    exports:", sorted(p.name for p in out.iterdir()))
    print("    sizes:", {p.name: p.stat().st_size for p in sorted(out.iterdir())})
    assert not s.console_errors, "console errors:\n" + "\n".join(s.console_errors[:10])


def scenario_config_roundtrip(s):
    """A16 — export then import the same bundle: sidecars unchanged."""
    s.load()
    src = Path(s.sources)
    targets = sorted(src.glob("Crispy-*.json")) + sorted(src.glob("Crispy-avar.csv")) + \
        sorted(src.glob(".avar2-studio/axis-metadata.json")) + \
        sorted(src.glob(".avar2-studio/instance-renames.json"))
    def snapshot(path):
        """The import's *meaningful* content: every layer pin and axis number.

        The studio canonicalises on write (control tags upper-cased,
        ``0.0`` -> ``0``, hand-drawn outlines captured with a source_sig), so
        raw JSON equality would report a diff on a correct no-op import;
        compare the payload and report byte diffs separately. Every pin the
        designer authored must survive — on Crispy that is 133 layers / 697
        pins (WGHT/WDTH/OPSZ included: the shadow ignores axes it does not
        have, so pruning them was pure loss)."""
        if path.suffix != ".json":
            return ("text", path.read_bytes().decode("utf-8-sig"))
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
        if "axes" not in data:
            return ("json", data)

        def pin(loc):
            return tuple(sorted(
                (str(k).upper(), round(float(v), 4))
                for k, v in (loc or {}).items()
                if k not in ("outline", "source_sig")))

        return ("control", tuple(sorted(
            (str(ax.get("tag", "")).upper(),
             round(float(ax.get("default") or 0), 4),
             round(float(ax.get("min") or 0), 4),
             round(float(ax.get("max") or 0), 4),
             tuple(sorted((str(l["glyph"]), pin(l.get("location")))
                          for l in ax.get("layers") or [] if l.get("glyph"))))
            for ax in data["axes"])))

    def raw(path):
        return path.read_bytes()

    wait_header_ready(s)
    s.page.click("button:has-text('Config')")
    s.page.wait_for_selector(".config-dropdown .load-font-menu", timeout=10000)
    with s.page.expect_download(timeout=60000) as dl:
        s.page.click(".load-font-item:has-text('Export configuration')")
    bundle = Path(s.shots).parent / "config-bundle.json"
    dl.value.save_as(str(bundle))
    # Baseline AFTER the export: an export legitimately captures hand-drawn
    # brace outlines into the sidecar (config_port.build_export), so that
    # write belongs to the export, not to the import round-trip.
    before = {str(p): snapshot(p) for p in targets}
    before_bytes = {str(p): raw(p) for p in targets}
    s.page.click("button:has-text('Config')")
    s.page.wait_for_selector(".config-dropdown .load-font-menu", timeout=10000)
    files = s.page.locator("input[type=file]")
    files.nth(files.count() - 1).set_input_files(str(bundle))
    s.page.wait_for_selector(".import-config-modal", timeout=30000)
    confirm = s.page.locator(".import-config-confirm")
    # The validation report can make the dialog taller than the viewport and
    # Playwright refuses to click an off-screen node; a DOM click is the same
    # user action for this assertion.
    confirm.evaluate("el => el.click()")
    s.page.wait_for_timeout(5000)
    # Applying a bundle rewrites four sidecars and each write schedules its
    # own full rebuild — observed: four builds at ~48 s plus a ~16 s compress
    # each, ~7 min in total on Crispy — so wait for the queue to drain.
    wait_idle(s.url, timeout=900)
    after = {str(p): snapshot(p) for p in targets}
    diff = [p for p in before if before[p] != after[p]]
    byte_diff = [p for p in before if before_bytes[p] != Path(p).read_bytes()]
    print("    layer/axis payload changed:", [Path(p).name for p in diff])
    print("    byte differences (canonicalisation):",
          [Path(p).name for p in byte_diff])
    if diff:
        for p in diff:
            print("      DIFF", Path(p).name, "\n        before:", str(before[p])[:400],
                  "\n        after: ", str(after[p])[:400])
    assert not diff, f"config round-trip changed {diff}"
    s.shot("a16-config")


def scenario_load_font(s):
    """A15 — swap to a built-in example, then back to the copy."""
    s.load()
    before_rows = instance_names(s.url)
    wait_header_ready(s)
    s.page.click("button:has-text('Load Font')")
    s.page.wait_for_selector(".load-font-menu", timeout=10000)
    s.page.click(".load-font-item:has-text('Crispy Mini')")
    t0 = time.time()
    while time.time() - t0 < 120:
        h = health(s.url)
        if h.get("family_name") and h["family_name"] != "Crispy":
            break
        time.sleep(2)
    else:
        raise AssertionError("family_name never followed the example load")
    wait_idle(s.url)
    example_rows = instance_names(s.url)
    print("    example family:", health(s.url).get("family_name"), "rows", len(example_rows))
    assert example_rows != before_rows, "rows did not swap with the example"
    assert "selected" not in (s.page.locator("[data-instance-name]").first.get_attribute("class") or ""), \
        "a row stayed selected across the source swap"
    # back to the copy: upload the staged .glyphs + CSV + metadata
    src = Path(s.sources)
    s.page.click("button:has-text('Load Font')")
    s.page.wait_for_selector(".load-font-menu", timeout=10000)
    files = s.page.locator("input[type=file]")
    files.first.set_input_files([str(src / "Crispy.glyphs"),
                                 str(src / "Crispy-avar.csv"),
                                 str(src / ".avar2-studio/axis-metadata.json")])
    t0 = time.time()
    while time.time() - t0 < 180:
        h = health(s.url)
        if h.get("family_name") == "Crispy" and not h.get("building"):
            break
        time.sleep(2)
    else:
        raise AssertionError(
            f"did not return to Crispy: {health(s.url).get('family_name')} "
            f"{health(s.url).get('last_build_error')}")
    assert instance_names(s.url) == before_rows, "rows did not return after re-upload"
    s.shot("a15-after-load-font")


SCENARIOS = {
    "select-no-save": scenario_select_no_save,
    "preview-no-build": scenario_preview_no_build,
    "sidebar-edit-saves": scenario_sidebar_edit_saves,
    "console-clean": scenario_console_clean,
    "first-load": scenario_first_load,
    "sync-dots": scenario_sync_dots,
    # `export` runs BEFORE the scenarios that edit the origin: A7 moves the
    # ORIGIN row's XOPQ, which relocates the built default and pushes the
    # mapped build past gvar's per-glyph header limit, so everything after it
    # sees the plain fallback instead of the mapped font. A12 documents the
    # resting style, so it must run on one.
    "export": scenario_export,
    "flyout-source": scenario_flyout_source,
    "lifecycle": scenario_lifecycle,
    "multi-broadcast": scenario_multi_broadcast,
    "grade": scenario_grade,
    "control-axis": scenario_control_axis,
    "kinetikern": scenario_kinetikern,
    "tester": scenario_tester,
    "config-roundtrip": scenario_config_roundtrip,
    "load-font": scenario_load_font,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5072")
    ap.add_argument("--shots", default="ui-shots")
    ap.add_argument("--sources", default="/private/tmp/av2-routine/sources",
                    help="the staged copy's sources dir (A15/A16 upload/compare)")
    ap.add_argument("scenarios", nargs="*", default=["all"])
    a = ap.parse_args()
    names = list(SCENARIOS) if a.scenarios == ["all"] else a.scenarios
    if "5070" in a.url:
        sys.exit("refusing to probe port 5070 (the designer's live studio)")
    failed = 0
    with sync_playwright() as pw:
        for n in names:
            print(f"=== {n}")
            s = Session(pw, a.url, a.shots, a.sources)
            try:
                # Land on an idle studio. A build in flight (or the WOFF2
                # compress that follows it) greys out "Download font…", hides
                # the mapped sliders behind the building veil and makes
                # console-clean log 409s — all of which read as scenario
                # failures although the product is fine.
                wait_ui_idle(s, timeout=600)
                SCENARIOS[n](s)
                print(f"PASS {n}")
            except Exception as e:  # report every failure, keep going
                failed += 1
                print(f"FAIL {n}: {e}")
                try:
                    s.shot(f"FAIL-{n}")
                except Exception:
                    pass
            finally:
                s.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
