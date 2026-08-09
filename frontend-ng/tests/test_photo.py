"""Profile photo: downscale on the client, upload, see it on the roster.

The whole point of doing the resize client-side is that a phone photo is megabytes but the roster
needs a thumbnail — so the DB stays tiny and the original never leaves the machine.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from gini.ui.main_window import _photo_data_url


def _app():
    return QApplication.instance() or QApplication([])


def test_a_big_photo_becomes_a_tiny_thumbnail():
    _app()
    img = QImage(2400, 1600, QImage.Format_RGB32)
    img.fill(QColor("steelblue"))
    p = os.path.join(tempfile.mkdtemp(), "big.png")
    img.save(p, "PNG")

    url = _photo_data_url(p)
    assert url.startswith("data:image/png;base64,")
    assert len(url) < 400_000                        # comfortably under the server's cap
    assert len(url) < 50_000                         # …in fact tiny — a 128px thumbnail


def test_a_non_image_yields_nothing_rather_than_crashing():
    _app()
    bad = os.path.join(tempfile.mkdtemp(), "notimage.png")
    with open(bad, "w") as f:
        f.write("this is not an image")
    assert _photo_data_url(bad) == ""
    assert _photo_data_url("/does/not/exist.png") == ""


def test_the_uploaded_photo_shows_up_on_the_roster(tmp_path):
    """End-to-end without HTTP: the store round-trips the data-URL and the roster join surfaces it."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "teaching-center"))
    import accounts as A
    import social as S
    import teacher as T
    from store import Store

    c = T.Course(tmp_path, "c1")
    Store(tmp_path).upsert_enrolment("ravi", name="Ravi", sis_id="", token="TOK", group="",
                                     ai_hosted=False)
    accts = A.Accounts(tmp_path)
    accts.claim("ravi", "TOK", "password123")

    _app()
    img = QImage(600, 600, QImage.Format_RGB32); img.fill(QColor("tomato"))
    p = os.path.join(tempfile.mkdtemp(), "face.png"); img.save(p, "PNG")
    url = _photo_data_url(p)

    assert accts.set_photo("ravi", url)["ok"]
    # the roster join the console reads
    soc = S.Social(tmp_path, c)
    assert accts.photo("ravi") == url
    assert soc.store.photo("ravi") == url            # same store, same value
