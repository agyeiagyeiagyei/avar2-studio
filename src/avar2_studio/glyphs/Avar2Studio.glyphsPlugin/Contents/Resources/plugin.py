# -*- coding: utf-8 -*-
"""avar2 Studio — Glyphs 3 General plugin.

One hub for the studio and its design tools, as a submenu of the Window
menu:

- Open in avar2 Studio — starts the studio on the font's file (or reuses
  a studio already serving it) and opens it in the browser.
- Stop avar2 Studio — ends the server this menu started.
- Corner Radii / Instance Delta / Parametric Masters / Slant Master /
  Width Matcher —
  toggle the reporters through Glyphs' own activate/deactivate API, so
  the check marks stay in step with View → Show ….
- Multi-Source Edit — selects the tool in the toolbar.

The launching itself lives in studio_launcher.py (pure Python, tested by
the studio's test-suite); this file is the Glyphs glue.
"""

import os
import sys
import traceback

import objc
from AppKit import NSApplicationWillTerminateNotification, NSMenu, NSMenuItem, NSOffState, NSOnState
from Foundation import NSNotificationCenter
from PyObjCTools import AppHelper
from GlyphsApp import Glyphs, WINDOW_MENU
from GlyphsApp.plugins import GeneralPlugin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_launcher  # noqa: E402

# (menu title, principal class of the bundle)
REPORTERS = (
    ("Corner Radii", "CornerRadii"),
    ("Instance Delta", "InstanceDelta"),
    ("Parametric Masters", "ParametricMasters"),
    ("Slant Master", "SlantMaster"),
    ("Width Matcher", "WidthMatcher"),
)
TOOL = ("Multi-Source Edit", "MultiSourceEdit")

DEBUG = False  # flip to True for /tmp instrumentation while developing
_DEBUG_LOG = "/tmp/avar2studio-debug.log"


def _dbg(msg):
    if not DEBUG:
        return
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write("%s\n" % msg)
            if msg == "EXCEPTION":
                f.write(traceback.format_exc())
    except Exception:
        pass


class Avar2Studio(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({"en": "avar2 Studio"})
        self.launcher = studio_launcher.StudioLauncher()

    @objc.python_method
    def start(self):
        try:
            self._installMenu()
            NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, "applicationWillTerminate:", NSApplicationWillTerminateNotification, None
            )
        except Exception:
            _dbg("EXCEPTION")

    # --- menu --------------------------------------------------------------

    @objc.python_method
    def _installMenu(self):
        menu = NSMenu.alloc().initWithTitle_(self.name)
        menu.addItem_(self._item("Open in avar2 Studio", "openStudio:"))
        menu.addItem_(self._item("Stop avar2 Studio", "stopStudio:"))
        menu.addItem_(NSMenuItem.separatorItem())
        for title, className in REPORTERS:
            menu.addItem_(self._item(title, "toggleReporter:", className))
        menu.addItem_(self._item(TOOL[0], "selectTool:", TOOL[1]))
        top = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(self.name, None, "")
        top.setSubmenu_(menu)
        Glyphs.menu[WINDOW_MENU].append(top)

    @objc.python_method
    def _item(self, title, action, represented=None):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            Glyphs.localize({"en": title}), action, ""
        )
        item.setTarget_(self)
        if represented is not None:
            item.setRepresentedObject_(represented)
        return item

    @objc.typedSelector(objc._C_NSBOOL + b"@:@")
    def validateMenuItem_(self, item):
        try:
            action = item.action()
            if action == "toggleReporter:":
                reporter = self._reporter(item.representedObject())
                active = reporter is not None and self._isActive(reporter)
                item.setState_(NSOnState if active else NSOffState)
                return reporter is not None
            if action == "openStudio:":
                return Glyphs.font is not None
            if action == "stopStudio:":
                return self.launcher.running
            if action == "selectTool:":
                return Glyphs.currentDocument is not None and self._toolClass() is not None
        except Exception:
            _dbg("EXCEPTION")
        return True

    # --- studio --------------------------------------------------------------

    def openStudio_(self, sender):
        try:
            font = Glyphs.font
            if font is None:
                self._notify("Open a font first.")
                return
            path = font.filepath
            if not path:
                self._notify("Save the font first: the studio opens the file on disk.")
                return
            self.launcher.open(
                path,
                on_ready=lambda url, note: AppHelper.callAfter(self._ready, url, note),
                on_error=lambda message: AppHelper.callAfter(self._failed, message),
            )
        except Exception:
            _dbg("EXCEPTION")

    def stopStudio_(self, sender):
        try:
            if self.launcher.stop():
                self._notify("avar2 Studio stopped.")
        except Exception:
            _dbg("EXCEPTION")

    def applicationWillTerminate_(self, notification):
        try:
            self.launcher.stop()
        except Exception:
            _dbg("EXCEPTION")

    @objc.python_method
    def _ready(self, url, note):
        if note == "started":
            self._notify("avar2 Studio is running at %s" % url)

    @objc.python_method
    def _failed(self, message):
        first = message.splitlines()[0] if message else "avar2 Studio could not start."
        self._notify(first)
        print("avar2 Studio: %s" % message)  # the rest (the server's log tail) goes to the Macro panel

    @objc.python_method
    def _notify(self, message):
        try:
            Glyphs.showNotification(self.name, message)
        except Exception:
            _dbg("EXCEPTION")

    # --- tools -----------------------------------------------------------------

    def toggleReporter_(self, sender):
        try:
            reporter = self._reporter(sender.representedObject())
            if reporter is None:
                self._notify("%s is not installed: run avar2-studio install-glyphs-plugins and restart Glyphs." % sender.title())
                return
            if self._isActive(reporter):
                Glyphs.deactivateReporter(reporter)
            else:
                Glyphs.activateReporter(reporter)
        except Exception:
            _dbg("EXCEPTION")

    def selectTool_(self, sender):
        try:
            toolClass = self._toolClass()
            if toolClass is None:
                self._notify("%s is not installed: run avar2-studio install-glyphs-plugins and restart Glyphs." % sender.title())
                return
            document = Glyphs.currentDocument
            if document is None:
                self._notify("Open a font first.")
                return
            document.windowController().setToolForClass_(toolClass)
        except Exception:
            _dbg("EXCEPTION")

    @objc.python_method
    def _reporter(self, className):
        """The loaded reporter instance whose principal class is ``className``."""
        try:
            for reporter in Glyphs.reporters:
                if reporter.className() == className:
                    return reporter
        except Exception:
            _dbg("EXCEPTION")
        return None

    @objc.python_method
    def _isActive(self, reporter):
        try:
            return any(r == reporter for r in Glyphs.activeReporters)
        except Exception:
            _dbg("EXCEPTION")
            return False

    @objc.python_method
    def _toolClass(self):
        try:
            return objc.lookUpClass(TOOL[1])
        except objc.nosuchclass_error:
            return None

    @objc.python_method
    def __file__(self):
        """Please leave this method unchanged"""
        return __file__
