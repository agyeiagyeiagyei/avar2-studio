"""Flask blueprints for the studio's HTTP/WS routes (audit §3 step e).

server.py constructs the app and owns the helpers, the watcher and
``main()``; every ``@app.route`` handler lives in one group module here
and is registered through ``register(app)`` at the bottom of server.py.
Handlers reach server-owned helpers through late-bound ``server.X``
attribute access — these modules load while server is mid-import, and
tests patch ``server.X`` — and shared runtime state through ``state.X``.
"""

from __future__ import annotations

from . import (
    avar2,
    build,
    config,
    control_axes,
    coverage,
    fontra,
    health,
    instances,
    kinetikern,
    load,
    source,
    text_width,
    transforms_grade,
    ui,
)

BLUEPRINTS = (ui.bp, source.bp, health.bp, build.bp, text_width.bp,
              coverage.bp, config.bp, transforms_grade.bp, kinetikern.bp,
              avar2.bp, instances.bp, control_axes.bp, load.bp,
              fontra.bp)


def register(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
