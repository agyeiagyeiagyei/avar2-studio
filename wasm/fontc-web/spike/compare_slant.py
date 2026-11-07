"""Oracle: the desktop slant engine vs the Rust port.

Runs avar2_studio.transforms.slanting.slant_font on the given source
with the given params, then compares every glyph layer of the Python
result against the Rust-transformed source — path count, node count,
node positions/types/smooth, component positions, anchor positions and
widths. Layers are matched by master NAME (Python twin ids are random
uuids, the Rust port's are deterministic) and braces by their
coordinates.
"""
import json
import sys

import glyphsLib


def layer_key(font, master_names, layer):
    lid = str(layer.layerId)
    if lid in master_names:
        return ("master", master_names[lid])
    coords = None
    attrs = getattr(layer, "attributes", None)
    if attrs is not None:
        try:
            coords = attrs.get("coordinates")
        except AttributeError:
            coords = None
    if isinstance(coords, (list, tuple)):
        return ("brace", tuple(round(float(v), 3) for v in coords))
    return ("backup", lid)


def _xy(obj):
    try:
        return float(obj.x), float(obj.y)
    except AttributeError:
        return float(obj[0]), float(obj[1])


def shapes_of(layer):
    return [
        [(float(n.position.x), float(n.position.y), str(n.type), bool(n.smooth))
         for n in p.nodes]
        for p in layer.paths
    ]


def components_of(layer):
    return [(str(c.name),) + _xy(c.position) for c in layer.components]


def anchors_of(layer):
    return sorted((str(a.name),) + _xy(a.position) for a in layer.anchors)


def compare(label, py_font, rs_font):
    py_masters = {str(m.id): str(m.name) for m in py_font.masters}
    rs_masters = {str(m.id): str(m.name) for m in rs_font.masters}
    assert sorted(py_masters.values()) == sorted(rs_masters.values()), (
        label, "master names differ", sorted(py_masters.values()), sorted(rs_masters.values()))
    assert [str(a.axisTag) for a in py_font.axes] == [str(a.axisTag) for a in rs_font.axes], label
    for pm in py_font.masters:
        rm = [m for m in rs_font.masters if str(m.name) == str(pm.name)]
        assert len(rm) == 1, (label, "master name not unique", pm.name)
        assert [float(v) for v in pm.axes] == [float(v) for v in rm[0].axes], (
            label, pm.name, list(pm.axes), list(rm[0].axes))

    worst = 0.0
    checked = 0
    for pg in py_font.glyphs:
        rg = rs_font.glyphs[pg.name]
        assert rg is not None, (label, "glyph missing", pg.name)
        py_layers = {}
        for L in pg.layers:
            py_layers.setdefault(layer_key(py_font, py_masters, L), []).append(L)
        rs_layers = {}
        for L in rg.layers:
            rs_layers.setdefault(layer_key(rs_font, rs_masters, L), []).append(L)
        for key, pls in py_layers.items():
            if key[0] == "backup":
                continue
            rls = rs_layers.get(key)
            assert rls is not None, (label, pg.name, "layer missing in rust output", key)
            assert len(pls) == len(rls) == 1, (label, pg.name, key, len(pls), len(rls))
            pl, rl = pls[0], rls[0]
            assert abs(float(pl.width) - float(rl.width)) < 1e-6, (label, pg.name, key, "width")
            ps, rs_ = shapes_of(pl), shapes_of(rl)
            assert len(ps) == len(rs_), (label, pg.name, key, "path count", len(ps), len(rs_))
            for pi, (pp, rp) in enumerate(zip(ps, rs_)):
                assert len(pp) == len(rp), (label, pg.name, key, pi, "node count", len(pp), len(rp))
                for ni, (pn, rn) in enumerate(zip(pp, rp)):
                    assert pn[2] == rn[2], (label, pg.name, key, pi, ni, "type", pn, rn)
                    assert pn[3] == rn[3], (label, pg.name, key, pi, ni, "smooth", pn, rn)
                    d = max(abs(pn[0] - rn[0]), abs(pn[1] - rn[1]))
                    worst = max(worst, d)
                    checked += 1
                    assert d == 0.0, (label, pg.name, key, pi, ni, "position", pn, rn)
            pc, rc = components_of(pl), components_of(rl)
            assert pc == rc, (label, pg.name, key, "components", pc, rc)
            checked += len(pc)
            pa, ra = anchors_of(pl), anchors_of(rl)
            assert pa == ra, (label, pg.name, key, "anchors", pa, ra)
            checked += len(pa)
    print("%s: %d nodes/components/anchors compared, worst node delta %.6f"
          % (label, checked, worst))


def main(source, plain_rs, plain_params, override_rs, override_params):
    from avar2_studio.transforms import slanting

    for label, rs_path, params_path in (("plain", plain_rs, plain_params),
                                        ("override", override_rs, override_params)):
        params = json.load(open(params_path))
        py_font = glyphsLib.GSFont(source)
        stats = slanting.slant_font(py_font, params)
        rs_font = glyphsLib.GSFont(rs_path)
        compare(label, py_font, rs_font)
        print("%s: python stats: slnt 0 to -%g over %d masters, %d twins" % (
            label, stats["max_angle"], stats["masters"], stats["twins"]))
    print("OK")


if __name__ == "__main__":
    main(*sys.argv[1:6])
