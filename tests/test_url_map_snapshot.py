"""T3.2: the route table is byte-stable across the blueprint split.

server.py's ``@app.route`` handlers move to Flask blueprints under
``routes/``; neither the URL rules nor their methods may change. This
snapshot (``str(rule)`` + sorted methods, sorted by rule) was captured
against the monolith and must stay green at every step of the move.
Endpoint names deliberately change to ``<blueprint>.<handler>`` — Flask
always prefixes blueprint endpoints.
"""

from avar2_studio import server

_EXPECTED = [
    '/ GET,HEAD,OPTIONS',
    '/<path:rest> GET,HEAD,OPTIONS',
    '/api/<path:rest> DELETE,GET,HEAD,OPTIONS,POST,PUT',
    '/api/avar2-font GET,HEAD,OPTIONS',
    '/api/avar2/axes GET,HEAD,OPTIONS',
    '/api/avar2/axis OPTIONS,POST',
    '/api/avar2/axis/<axis_name> DELETE,OPTIONS',
    '/api/avar2/axis/<axis_name> OPTIONS,PUT',
    '/api/avar2/instances GET,HEAD,OPTIONS',
    '/api/avar2/mapping/<instance_name>/<axis_name> OPTIONS,PUT',
    '/api/axes GET,HEAD,OPTIONS',
    '/api/build OPTIONS,POST',
    '/api/build/<job_id> DELETE,OPTIONS',
    '/api/build/<job_id> GET,HEAD,OPTIONS',
    '/api/build/status GET,HEAD,OPTIONS',
    '/api/config/export GET,HEAD,OPTIONS',
    '/api/config/import OPTIONS,POST',
    '/api/control-axes GET,HEAD,OPTIONS',
    '/api/control-axes OPTIONS,POST',
    '/api/control-axes/<tag> DELETE,OPTIONS',
    '/api/control-axes/<tag> OPTIONS,PATCH',
    '/api/control-axes/<tag>/layers OPTIONS,PUT',
    '/api/control-axes/<tag>/layers/delta OPTIONS,POST',
    '/api/control-axes/<tag>/open-editor OPTIONS,POST',
    '/api/control-axes/<tag>/reference-metrics GET,HEAD,OPTIONS',
    '/api/control-axes/<tag>/reseed OPTIONS,POST',
    '/api/examples GET,HEAD,OPTIONS',
    '/api/export-font OPTIONS,POST',
    '/api/export/<job_id>/download GET,HEAD,OPTIONS',
    '/api/font GET,HEAD,OPTIONS',
    '/api/fontra-shim-config GET,HEAD,OPTIONS',
    '/api/glyph-coverage GET,HEAD,OPTIONS',
    '/api/glyphs-file-status GET,HEAD,OPTIONS',
    '/api/health GET,HEAD,OPTIONS',
    '/api/instance OPTIONS,POST',
    '/api/instance/<instance_name> DELETE,OPTIONS',
    '/api/instance/<instance_name> OPTIONS,PUT',
    '/api/instance/<instance_name>/add-to-source OPTIONS,POST',
    '/api/instance/<instance_name>/editing DELETE,OPTIONS',
    '/api/instance/<instance_name>/editing OPTIONS,POST',
    '/api/instance/<instance_name>/rename OPTIONS,PUT',
    '/api/instances GET,HEAD,OPTIONS',
    '/api/instances/<instance_name>/grade DELETE,OPTIONS',
    '/api/instances/<instance_name>/grade OPTIONS,PUT',
    '/api/instances/order OPTIONS,PUT',
    '/api/kinetikern GET,HEAD,OPTIONS',
    '/api/kinetikern OPTIONS,PUT',
    '/api/kinetikern/all OPTIONS,PUT',
    '/api/kinetikern/instances/<instance_name> DELETE,OPTIONS',
    '/api/kinetikern/instances/<instance_name> OPTIONS,PUT',
    '/api/kinetikern/propose GET,HEAD,OPTIONS',
    '/api/load-source OPTIONS,POST',
    '/api/mapped-location GET,HEAD,OPTIONS',
    '/api/masters GET,HEAD,OPTIONS',
    '/api/text-width GET,HEAD,OPTIONS',
    '/api/transforms GET,HEAD,OPTIONS',
    '/api/transforms OPTIONS,PUT',
    '/api/transforms/grade GET,HEAD,OPTIONS',
    '/api/transforms/grade OPTIONS,PUT',
    '/asset-manifest.json GET,HEAD,OPTIONS',
    '/data/<path:subpath> GET,HEAD,OPTIONS',
    '/favicon.ico GET,HEAD,OPTIONS',
    '/fontra/ DELETE,GET,HEAD,OPTIONS,POST,PUT',
    '/fontra/<path:subpath> DELETE,GET,HEAD,OPTIONS,POST,PUT',
    '/images/<path:subpath> GET,HEAD,OPTIONS',
    '/lang/<path:subpath> GET,HEAD,OPTIONS',
    '/manifest.json GET,HEAD,OPTIONS',
    '/projectlist GET,HEAD,OPTIONS',
    '/serverinfo GET,HEAD,OPTIONS',
    '/static/<path:filename> GET,HEAD,OPTIONS',
    '/webfonts/<path:subpath> GET,HEAD,OPTIONS',
    '/websocket GET,HEAD,OPTIONS',
]


def _rules(app):
    return sorted(
        f"{r} {','.join(sorted(r.methods or []))}"
        for r in app.url_map.iter_rules()
    )


def test_url_map_matches_the_monolith_snapshot():
    assert _rules(server.app) == _EXPECTED
