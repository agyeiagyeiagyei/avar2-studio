"""Fontra proxy routes (audit §3 step e group).

Reverse proxy: avar2-studio at :5070 fronts Fontra at :8001.

Why proxy: cross-origin iframes can't share state with the parent
(no CSS injection, no JS bridging). To present a FOCUSED Fontra UI
— hiding the panels irrelevant to a control-axis brace edit — we
serve Fontra under our own origin and inject a tiny stylesheet on
the way through. Same-origin also unblocks future bidirectional
postMessage if we want to bridge state.

Two legs:
  - HTTP:  /fontra/<path>  → http://127.0.0.1:8001/<path>
           HTML responses get a stylesheet appended that hides
           the panels we don't want, and absolute-path references
           (``href="/css/..."``) get rewritten to live under
           /fontra/.
  - WS:    /websocket?...  → ws://127.0.0.1:8001/websocket?...
           (Fontra's JS builds the ws URL from window.location.host
           so it lands here, not on Fontra's port directly.)
"""

from string import Template

from flask import Blueprint, Response, jsonify, request
from flask_sock import Sock

from .. import server, state
from ..studio_log import get_logger

log = get_logger(__name__)

bp = Blueprint("fontra", __name__)

# flask_sock's Sock used purely as a decorator factory: ``bp=bp`` puts the
# websocket rule on this blueprint (flask_sock supports blueprints via the
# ``bp`` kwarg), so no app-level Sock wiring is needed.
_sock = Sock()


# Focused-UI CSS for the embedded Fontra editor. Hides the
# sidebar panels + edit tools that aren't useful for a
# brace-layer edit. Selectors map to Fontra's own DOM:
#   - Sidebar tabs use ``.sidebar-tab[data-sidebar-name="..."]``
#     keyed by the panel class's ``identifier`` property
#     (views-editor/src/panel-*.js).
#   - Edit tools use ``[data-tool="..."]`` from
#     edit-tools-*.js identifier properties.
#   - Selectors are paired with their .sidebar-content / wrapper
#     equivalents so the panel disappears whether it was the
#     currently-open panel or not.
#
# If Fontra renames panels/tools we'll have to update; these are
# documented in their identifier properties so changes are easy
# to track.
#
# The CSS is built per session: a STUDIO-axis session keeps the
# designspace-navigation panel visible — its glyph-sources list is
# the multi-source batch-editing surface, trimmed to studio layers
# by the shim script below — while every other session hides it
# along with the rest.


def _fontra_focus_css() -> str:
    show_nav = bool((state.FONTRA_EDITOR_SESSION or {}).get("studio"))
    hidden = [
        "text-entry", "selection-info", "reference-font", "glyph-search",
        "selection-transformation", "glyph-note", "related-glyphs",
        "characters-glyphs",
    ]
    if not show_nav:
        hidden.insert(1, "designspace-navigation")
    panel_rules = ",\n  ".join(
        f'.sidebar-tab[data-sidebar-name="{n}"],\n'
        f'  .sidebar-content[data-sidebar-name="{n}"]'
        for n in hidden
    )
    # In studio sessions the sidebar containers must stay visible so
    # the designspace-navigation panel has somewhere to live; the
    # NOTE about shadow DOM still applies (accordion internals are
    # trimmed by the shim, not this stylesheet).
    if show_nav:
        chrome_rules = ".top-bar-container,\n  menu-bar"
    else:
        chrome_rules = (
            ".top-bar-container,\n  .sidebar-container.left,\n"
            "  .sidebar-container.right,\n  menu-bar"
        )
    return Template(server._read_asset("fontra/focus.css")).substitute(
        panel_rules=panel_rules, chrome_rules=chrome_rules)


# Studio-session shim: runs inside the proxied Fontra page (same
# origin, so it can reach our /api and Fontra's DOM alike). Active
# only when /api/fontra-shim-config says the session is a
# studio-axis edit. It:
#   1. opens the designspace-navigation sidebar tab,
#   2. trims the panel to the glyph-sources accordion (no font axes,
#      no layers list, no add/remove source buttons),
#   3. sorts the sources list by source name (Fontra's own
#      "by-source-name" option), so the master/instance-labelled studio
#      layers read A→Z by style,
#   4. hides every sources-list row that is not a STUDIO layer of the
#      session's axis (masters, font sources, other axes' layers),
#   5. keeps multi-source editing OFF — the designer toggles rows on
#      deliberately; an opened layer edits alone by default. Masters and
#      source layers are never editing targets (Fontra's own init can put
#      the selected source there, so that strip keeps re-applying).
# A row is a studio layer when its dense location sits OFF the
# session axis's default — only sidecar seeding creates such layers
# in the shadow. Fallback: match the seed-time source-name label
# ("<name>" or, for unnamed locations, "<tag> <value>" coordinates).
# If no row matches, the list is left untrimmed (fail open) and
# nothing is auto-enabled (fail closed).
# T3.4: the implementation is SHARED with the browser embed.
# fontra/embed-shim.js is a build artifact of fontra-embed's
# src/{metrics-hud,focused-ui,studio-shim-bootstrap}.js (provenance
# header in the file; rebuild with fontra-embed's build-studio-shim.mjs
# and re-copy) and is the injected shim when present. fontra/shim.js,
# the pre-T3.4 desktop-only shim, is the fallback when the built copy
# is absent. The <script> wrapper belongs to the injection, not the
# file.
def _studio_shim_script() -> str:
    try:
        source = server._read_asset("fontra/embed-shim.js")
    except FileNotFoundError:
        source = server._read_asset("fontra/shim.js")
    return '\n<script id="avar2-studio-fontra-shim">\n' + source + '\n</script>\n'


def _is_html_response(headers) -> bool:
    ctype = headers.get("Content-Type", "")
    return "text/html" in ctype.lower()


def _is_css_response(headers) -> bool:
    ctype = headers.get("Content-Type", "")
    return "text/css" in ctype.lower()


def _rewrite_html_paths(body: bytes) -> bytes:
    """Rewrite absolute-path URL references in proxied Fontra HTML
    so they resolve under our ``/fontra/`` mount instead of root.
    Also injects the focused-UI stylesheet just before ``</head>``."""
    text = body.decode("utf-8", errors="replace")
    # ``href="/x"`` / ``src="/x"`` → keep "/fontra/x"
    text = text.replace('href="/', 'href="/fontra/')
    text = text.replace("href='/", "href='/fontra/")
    text = text.replace('src="/', 'src="/fontra/')
    text = text.replace("src='/", "src='/fontra/")
    # JS import map — Fontra ships ``"fontra/": "/"`` so any
    # ``import "fontra/core/..."`` resolves to ``/core/...`` on the
    # parent origin. With us proxying Fontra under ``/fontra/`` we
    # need to point that import map at ``/fontra/`` instead, or
    # every JS module load 404s and the canvas never initialises.
    text = text.replace('"fontra/": "/"', '"fontra/": "/fontra/"')
    text = text.replace("'fontra/': '/'", "'fontra/': '/fontra/'")
    # Inject the focused-UI CSS + studio shim before </head>;
    # tolerate uppercase. The CSS is session-aware (see
    # _fontra_focus_css); the shim self-deactivates unless
    # /api/fontra-shim-config marks the session as a studio edit.
    for needle in ("</head>", "</HEAD>"):
        if needle in text:
            text = text.replace(
                needle, _fontra_focus_css() + _studio_shim_script() + needle, 1
            )
            break
    return text.encode("utf-8")


def _rewrite_css_paths(body: bytes) -> bytes:
    """Rewrite ``url(/x)`` references in proxied Fontra CSS."""
    text = body.decode("utf-8", errors="replace")
    text = text.replace("url(/", "url(/fontra/")
    text = text.replace('url("/', 'url("/fontra/')
    text = text.replace("url('/", "url('/fontra/")
    return text.encode("utf-8")


def _proxy_to_fontra(upstream_path: str):
    """Shared proxy mechanic — forward an HTTP request to Fontra and
    return its response, rewriting HTML/CSS path references and
    injecting focused-UI CSS into HTML on the way back. Called from
    every route that forwards to Fontra: /fontra/* and the
    root-level runtime-fetch paths Fontra needs (/lang, /data,
    /images, /webfonts, /projectlist, /serverinfo, /api/*).
    """
    if state.FONTRA_PROCESS is None or state.FONTRA_PROCESS.poll() is not None:
        return jsonify({"error": "Fontra subprocess is not running"}), 503

    import urllib.error
    import urllib.request

    upstream = f"http://127.0.0.1:{state.FONTRA_PORT}{upstream_path}"
    if request.query_string:
        upstream += "?" + request.query_string.decode("utf-8")

    req = urllib.request.Request(
        upstream,
        method=request.method,
        data=request.get_data() if request.method in ("POST", "PUT", "PATCH") else None,
    )
    # Forward useful request headers; skip Host (we're the new host).
    for header, value in request.headers.items():
        if header.lower() in ("host", "content-length"):
            continue
        req.add_header(header, value)

    try:
        upstream_resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        upstream_resp = exc  # serve error body too
    except Exception as exc:
        return jsonify({"error": f"Fontra proxy failed: {exc}"}), 502

    raw_body = upstream_resp.read()
    headers = dict(upstream_resp.headers.items())

    if _is_html_response(headers):
        body = _rewrite_html_paths(raw_body)
        headers["Content-Length"] = str(len(body))
    elif _is_css_response(headers):
        body = _rewrite_css_paths(raw_body)
        headers["Content-Length"] = str(len(body))
    else:
        body = raw_body

    # Strip headers that browsers may reject when we re-emit them.
    for h in ("Transfer-Encoding", "Connection"):
        headers.pop(h, None)

    return Response(body, status=upstream_resp.status, headers=headers)


@bp.route('/fontra/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
@bp.route('/fontra/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def fontra_http_proxy(subpath: str):
    """Main Fontra proxy. Iframe loads /fontra/editor.html?... and
    everything resolves under this prefix via the HTML/CSS path
    rewrites injected on the way back."""
    return _proxy_to_fontra("/" + subpath)


# Fontra's frontend issues runtime fetches against root paths the
# importmap rewrite can't reach (the bundle calls things like
# ``fetch("/lang/en.js")`` directly). Each of those needs to
# resolve through our origin to keep Fontra same-origin with the
# iframe parent. The collision-prone /api/* is handled by the
# catch-all below — Flask routes specific avar2-studio /api paths
# first and only falls through to the catch-all when nothing else
# matches.
@bp.route('/lang/<path:subpath>', methods=['GET'])
def fontra_lang_proxy(subpath: str):
    return _proxy_to_fontra(f"/lang/{subpath}")


@bp.route('/data/<path:subpath>', methods=['GET'])
def fontra_data_proxy(subpath: str):
    return _proxy_to_fontra(f"/data/{subpath}")


@bp.route('/images/<path:subpath>', methods=['GET'])
def fontra_images_proxy(subpath: str):
    return _proxy_to_fontra(f"/images/{subpath}")


@bp.route('/webfonts/<path:subpath>', methods=['GET'])
def fontra_webfonts_proxy(subpath: str):
    return _proxy_to_fontra(f"/webfonts/{subpath}")


@bp.route('/projectlist', methods=['GET'])
def fontra_projectlist_proxy():
    return _proxy_to_fontra("/projectlist")


@bp.route('/serverinfo', methods=['GET'])
def fontra_serverinfo_proxy():
    return _proxy_to_fontra("/serverinfo")


# Catch-all for /api/<anything> that doesn't match an avar2-studio
# route. Flask's routing prefers more-specific rules, so
# /api/glyph-coverage / /api/control-axes/<tag> / etc. match
# before this catch-all and stay on avar2-studio. Anything Fontra
# requests through /api/ (export, etc.) falls through here.
@bp.route('/api/<path:rest>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def fontra_api_fallthrough(rest: str):
    return _proxy_to_fontra(f"/api/{rest}")


# Root-level catch-all. Fontra's client bundle ships chunk JS
# (``230.chunk.js``) and WASM modules (``<hash>.wasm``,
# ``<hash>.module.wasm``) at the root of its content tree —
# these are dynamically imported by the editor and aren't
# referenced from the editor.html we rewrite. Without this
# fall-through they 404 against avar2-studio's root, the editor
# fails to init HarfBuzz / lazy chunks, and the canvas comes up
# blank.
#
# Flask/Werkzeug routes by specificity: all specific avar2-studio
# routes (``/``, ``/static/<path>``, ``/manifest.json``,
# ``/favicon.ico``, every ``/api/*``, ``/fontra/*``, ``/lang/*``,
# ``/data/*``, ``/images/*``, ``/webfonts/*``, ``/projectlist``,
# ``/serverinfo``) match first. Only paths none of those claim
# fall through here — i.e. exactly the Fontra runtime files we
# want to forward.
@bp.route('/<path:rest>', methods=['GET'])
def fontra_root_fallthrough(rest: str):
    return _proxy_to_fontra(f"/{rest}")


@_sock.route('/websocket', bp=bp)
def fontra_ws_proxy(ws):
    """Proxy WebSocket traffic to Fontra. The frontend opens
    ``ws://<host>/websocket?...``; we connect upstream to
    ``ws://127.0.0.1:8001/websocket?...`` with the same query string
    and relay messages in both directions until either side hangs up.
    """
    if state.FONTRA_PROCESS is None or state.FONTRA_PROCESS.poll() is not None:
        ws.close()
        return

    import websocket as ws_client_lib  # websocket-client; pulled in by flask-sock indirectly
    qs = request.query_string.decode("utf-8")
    upstream_url = f"ws://127.0.0.1:{state.FONTRA_PORT}/websocket"
    if qs:
        upstream_url += "?" + qs

    upstream = ws_client_lib.create_connection(upstream_url, timeout=30)
    upstream.settimeout(0.05)

    import threading as _threading

    def pump_upstream_to_client():
        try:
            while True:
                try:
                    msg = upstream.recv()
                except ws_client_lib._exceptions.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if msg is None:
                    break
                ws.send(msg)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    pumper = _threading.Thread(target=pump_upstream_to_client, daemon=True)
    pumper.start()

    try:
        while True:
            msg = ws.receive(timeout=None)
            if msg is None:
                break
            upstream.send(msg)
    except Exception:
        pass
    finally:
        try:
            upstream.close()
        except Exception:
            pass


@bp.route('/api/fontra-shim-config')
def fontra_shim_config():
    """Session config for the shim injected into proxied Fontra
    pages. Must be registered before Fontra's /api catch-all so the
    specific rule wins."""
    session = dict(state.FONTRA_EDITOR_SESSION or {"studio": False})
    # Desktop studio sessions allow edits to "→ computed" correction
    # targets: writes into the shadow self-heal on the next regen. The
    # browser embed leaves the flag unset — its bridge guards computed
    # layers read-only. The shared shim (fontra/embed-shim.js) reads
    # it; the fontra/shim.js fallback never strips studio rows anyway.
    if session.get("studio"):
        session["allow_computed_edits"] = True
    return jsonify(session)
