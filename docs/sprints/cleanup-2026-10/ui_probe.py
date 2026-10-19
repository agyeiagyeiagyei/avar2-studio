#!/usr/bin/env python3
"""Headless UI probes for a locally running avar2-studio (Playwright, Chromium).

Run against a studio that serves a COPY of the sources (never port 5070):

    python3 -I ui_probe.py --url http://localhost:5072 all
    python3 -I ui_probe.py --url http://localhost:5072 select-no-save preview-no-build

Needs the Python Playwright package with a Chromium download
(`pip install playwright && playwright install chromium`). Every scenario
prints the non-polling requests it saw with a timestamp, then PASS/FAIL
with the reason. Screenshots go to --shots (default ./ui-shots).

Scenarios:
  select-no-save     clicking an instance row must not PUT or rebuild
  preview-no-build   Preview sliders only call /api/mapped-location; Reset restores defaults
  sidebar-edit-saves a sidebar edit PUTs once (csv_only), no rebuild until Preview opens,
                     then exactly one build (this one triggers a real build on the copy)
  console-clean      load every main tab; zero console errors
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

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
        if not h.get("building"):
            return h
        time.sleep(2)
    raise TimeoutError("studio still building")


class Session:
    def __init__(self, pw, url, shots):
        self.url = url
        self.shots = Path(shots); self.shots.mkdir(parents=True, exist_ok=True)
        self.browser = pw.chromium.launch()
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 1000})
        self.t0 = time.time()
        self.requests = []
        self.console_errors = []
        self.page.on("request", self._on_request)
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.console_errors.append(str(e)))

    def _on_request(self, r):
        if any(s in r.url for s in POLL):
            return
        line = f"[{time.time()-self.t0:6.2f}s] {r.method} {r.url.replace(self.url, '')[:120]}"
        self.requests.append((r.method, r.url))
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


SCENARIOS = {
    "select-no-save": scenario_select_no_save,
    "preview-no-build": scenario_preview_no_build,
    "sidebar-edit-saves": scenario_sidebar_edit_saves,
    "console-clean": scenario_console_clean,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5072")
    ap.add_argument("--shots", default="ui-shots")
    ap.add_argument("scenarios", nargs="*", default=["all"])
    a = ap.parse_args()
    names = list(SCENARIOS) if a.scenarios == ["all"] else a.scenarios
    if "5070" in a.url:
        sys.exit("refusing to probe port 5070 (the designer's live studio)")
    failed = 0
    with sync_playwright() as pw:
        for n in names:
            print(f"=== {n}")
            s = Session(pw, a.url, a.shots)
            try:
                SCENARIOS[n](s)
                print(f"PASS {n}")
            except Exception as e:  # noqa: BLE001 — report every failure, keep going
                failed += 1
                print(f"FAIL {n}: {e}")
            finally:
                s.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
