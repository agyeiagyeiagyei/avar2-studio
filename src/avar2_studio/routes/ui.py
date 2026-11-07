"""UI routes — serving the bundled React build (audit §3 step e group)."""

from pathlib import Path

from flask import Blueprint, Response, jsonify, send_from_directory

# Directory of the bundled React build that gets shipped inside the
# wheel (populated by the Release CI). Routes below serve it under
# the same origin as the API so the installed package is a single
# self-contained command — no separate ``serve``/``npm start`` step.
_BUNDLE_DIR = Path(__file__).parent.parent / "static"

bp = Blueprint("ui", __name__)


@bp.route('/')
def _serve_ui_index():
    index_html = _BUNDLE_DIR / "index.html"
    if not index_html.exists():
        return (
            "<h1>Frontend bundle not present</h1>"
            "<p>This install of avar2-studio is missing the React bundle. "
            "Run <code>avar2-studio doctor</code> for setup help, or build "
            "the frontend yourself (<code>cd frontend &amp;&amp; npm ci &amp;&amp; "
            "npm run build</code>) if you cloned from source.</p>",
            503,
        )
    # Mark the HTML as backend-served. The app normally decides between
    # server and static mode by probing /api/health; on a static host that
    # probe 404s and Chrome logs a console error on every visit. The marker
    # makes the server case synchronous and silent (the static host serves
    # the same index.html without it).
    html = index_html.read_text(encoding="utf-8")
    marker = '<meta name="avar2-backend" content="1">'
    if marker not in html:
        html = html.replace("</head>", f"  {marker}\n</head>", 1)
    resp = Response(html, mimetype="text/html")
    # The index names hashed bundle assets, so it must never be served from a
    # stale cache across a studio upgrade; no-store is the right answer here
    # (rather than the conditional/ETag handling send_file provided).
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route('/static/<path:filename>')
def _serve_ui_static_asset(filename):
    """Vite-built JS/CSS/images, served from the bundle root.

    The CRA build nested assets under ``static/static/``; the Vite bundle
    keeps them in ``static/assets/`` with URLs already rooted at
    ``/static/`` (vite.config's ``base``), so the route serves the bundle
    directory itself."""
    return send_from_directory(str(_BUNDLE_DIR), filename)


@bp.route('/asset-manifest.json')
def _serve_ui_asset_manifest():
    return send_from_directory(str(_BUNDLE_DIR), "asset-manifest.json")


@bp.route('/manifest.json')
def _serve_ui_manifest():
    target = _BUNDLE_DIR / "manifest.json"
    if not target.exists():
        return jsonify({}), 404
    return send_from_directory(str(_BUNDLE_DIR), "manifest.json")


@bp.route('/favicon.ico')
def _serve_ui_favicon():
    target = _BUNDLE_DIR / "favicon.ico"
    if not target.exists():
        return ('', 204)  # silent: no favicon in the bundle is fine
    return send_from_directory(str(_BUNDLE_DIR), "favicon.ico")
