"""Generate the Cue Cards feature-tour screenshots from the real gBuilder UI, per theme.

Renders curated portions of the actual interface (no mockups) to PNGs under
``src/gini/ui/assets/cue/<theme-slug>/<kind>.png`` — one set per theme, one image per card
in cue_cards.FEATURE_CARDS — so the tour matches whatever theme GINI is running. Re-run
after UI changes (best on macOS for native fonts)::

    QT_QPA_PLATFORM=offscreen python -m scripts.gen_cue_shots
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# write any settings to a throwaway home so the generator never changes the user's theme
os.environ["GINI_HOME_DIR"] = tempfile.mkdtemp(prefix="gini-genshots-")

SS = 2          # supersample factor — captures at 2x so the cards stay sharp on Retina

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parents[1] / "src" / "gini" / "ui" / "assets" / "cue"
THEMES = ("Dark", "Light", "GINI Brand", "High Contrast", "Sand", "Blue", "Green")


def theme_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "dark").lower()) or "dark"


def _bg(theme) -> int:
    c = theme.theme.bg
    from gini.ui.cue_cards import _qcolor
    return _qcolor(c).rgb()


def _save(img: QImage, name: str, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    img.save(str(outdir / f"{name}.png"))


def _scene_shot(scene, name, outdir, bg, scale=2.8, pad=26) -> None:
    rect = scene.itemsBoundingRect().adjusted(-pad, -pad, pad, pad)
    img = QImage(max(1, int(rect.width() * scale)), max(1, int(rect.height() * scale)),
                 QImage.Format_ARGB32)
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    scene.render(p, QRectF(img.rect()), rect); p.end()
    _save(img, name, outdir)


def _widget_shot(widget, name, outdir, bg, w, h) -> None:
    # render through a painter scaled by SS so the PNG is high-res regardless of the
    # platform's device-pixel-ratio (grab() alone is 1x offscreen on Linux).
    widget.resize(w, h)
    QApplication.processEvents()
    sz = widget.size()                              # actual size (clamps to the size hint)
    img = QImage(sz.width() * SS, sz.height() * SS, QImage.Format_ARGB32)
    img.fill(bg)
    p = QPainter(img)
    p.scale(SS, SS)
    widget.render(p, QPoint(0, 0))
    p.end()
    _save(img, name, outdir)


def _clear_warnings(win) -> None:
    win.ctx.warnings = {}
    for n in win.canvas.scene_.nodes.values():
        n.update()


def _generate(win, app, outdir: Path) -> None:
    """Render the full set of curated shots for the currently-applied theme."""
    api = win.api
    scene = win.canvas.scene_
    bg = _bg(win.theme)

    def reset():
        for did in list(win.ctx.topology.devices):
            api.remove_device(did)

    # --- welcome / canvas / run: a small topology ---------------------------
    reset()
    h = api.add_device("host", x=-40, y=120)["id"]
    s = api.add_device("switch", x=180, y=120)["id"]
    r = api.add_device("router", x=400, y=120)["id"]
    db = api.add_device("database", x=620, y=20)["id"]
    wa = api.add_device("web_app", x=620, y=220)["id"]
    for a, b in [(h, s), (s, r), (r, db), (r, wa)]:
        win.ctx.topology.add_link(a, b)
        win.ctx.bus.link_added.emit(list(win.ctx.topology.links)[-1])
    app.processEvents(); _clear_warnings(win)
    _scene_shot(scene, "welcome", outdir, bg)
    _scene_shot(scene, "canvas", outdir, bg)
    for n in scene.nodes.values():
        n.set_status("running")
    app.processEvents()
    _scene_shot(scene, "run", outdir, bg)

    # --- X-ray / Wizard: ghost ring around a router -------------------------
    reset()
    rr = api.add_device("router", x=120, y=120)["id"]
    app.processEvents(); _clear_warnings(win)
    try:
        win.canvas._lp_node = scene.nodes[rr]
        win.canvas._fire_xray(); app.processEvents()
        _scene_shot(scene, "wizard", outdir, bg, scale=2.4)
        win.canvas.clear_xray()
    except Exception as e:
        print("wizard skipped:", e)

    # --- cloud & VPCs: a VPC box with services ------------------------------
    reset()
    vpc = api.add_device("vpc", x=20, y=20)["id"]
    win.ctx.topology.devices[vpc].properties["Name"] = "prod"
    api.add_device("database", x=150, y=150); api.add_device("cache", x=150, y=250)
    app.processEvents(); _clear_warnings(win)
    scene._on_device_changed(vpc)
    _scene_shot(scene, "cloud", outdir, bg)

    # --- serverless: the Function inspector ---------------------------------
    reset()
    fid = api.add_device("function")["id"]
    api.set_property(fid, "Handler", "custom")
    win.ctx.select(fid); win.inspector.set_live_running(True); app.processEvents()
    _widget_shot(win.inspector, "serverless", outdir, bg, 360, 560)

    # --- cost meter: the dashboard strip ------------------------------------
    from gini.domain.pricing import bill
    for tk in ("k8s_cluster", "pod", "function", "database", "host"):
        api.add_device(tk)
    win.dashboard.set_estimate(bill(win.ctx.topology, win.ctx.settings.prices))
    app.processEvents()
    _widget_shot(win.dashboard, "cost", outdir, bg, 760, 120)

    # --- live metrics -------------------------------------------------------
    try:
        import math
        from gini.ui.live_metrics import LiveMetrics
        lm = LiveMetrics(win.theme)
        for i in range(60):
            lm.push(40 + 30 * math.sin(i / 6), 300 + 120 * math.sin(i / 9),
                    50 + 40 * math.sin(i / 5), 8 + 5 * math.sin(i / 7))
        _widget_shot(lm, "metrics", outdir, bg, 360, 300)
    except Exception as e:
        print("metrics skipped:", e)

    # --- ask GINI: the assistant panel --------------------------------------
    try:
        _widget_shot(win.assistant, "ai", outdir, bg, 380, 420)
    except Exception as e:
        print("ai skipped:", e)

    # --- settings: the tabbed dialog ----------------------------------------
    from gini.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(win, win.ctx.settings)
    _widget_shot(dlg, "settings", outdir, bg, 480, 360)

    # --- router lab (best effort) -------------------------------------------
    try:
        reset()
        rid = api.add_device("router")["id"]
        win.ctx.select(rid); win._open_router_lab(rid)
        _widget_shot(win._router_lab, "router", outdir, bg, 560, 420)
        win._router_lab.close()
    except Exception as e:
        print("router skipped:", e)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from gini.ui.main_window import MainWindow
    win = MainWindow(app)
    for name in THEMES:
        win.theme.set_theme(name)
        app.processEvents()
        outdir = OUT / theme_slug(name)
        _generate(win, app, outdir)
        print("theme done:", name, "->", outdir.name)
    print("all done ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
