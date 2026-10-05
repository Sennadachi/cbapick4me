"""Web-mode GUI tests using NiceGUI's simulated `user` fixture."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

from cbapick4me.gui import app as gui

from .conftest import FIXTURES


@pytest.fixture
def web_page():
    gui._register(web=True)


async def _upload(user: User, data: bytes, name: str) -> None:
    from nicegui.elements.upload_files import SmallFileUpload

    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([SmallFileUpload(name=name, content_type="text/csv", _data=data)])


async def _wait(cond, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_web_upload_pick_download(user: User, web_page, fake_supplier):
    await user.open("/")
    await user.should_see("Drop BOM")
    await _upload(user, (FIXTURES / "wayfinder.csv").read_bytes(), "BOM_Wayfinder.csv")
    await user.should_see("22 capacitors, 25 resistors, 22 other lines")

    user.find("Next: exceptions").click()
    await user.should_see("Select rows to give them an exception")

    user.find("Pick parts").click()
    await user.should_see("Done — 14 lines searched", retries=40)
    await user.should_see("1 line(s) not picked")

    user.find("Download CSV").click()
    await _wait(lambda: user.download.http_responses)
    resp = user.download.http_responses[-1]
    text = resp.content.decode("utf-8-sig")
    assert "FAKE-C0603-100nF" in text and "DIODES(美台)" in text


async def test_web_sessions_are_isolated(create_user, web_page):
    a: User = create_user()
    b: User = create_user()
    await a.open("/")
    await b.open("/")
    await _upload(a, (FIXTURES / "wayfinder.csv").read_bytes(), "a.csv")
    await a.should_see("22 capacitors")
    await b.should_not_see("22 capacitors")


async def test_web_rejects_large_bom(user: User, web_page):
    header = "Designator,Value,Footprint\n"
    data = (header + "".join(f"C{i},100n,C0603\n" for i in range(1, 600))).encode()
    await user.open("/")
    await _upload(user, data, "big.csv")
    await user.should_see("this server allows 500")


async def test_web_requires_keys_for_digikey(user: User, web_page):
    await user.open("/")
    await _upload(user, (FIXTURES / "wayfinder.csv").read_bytes(), "w.csv")
    await user.should_see("22 capacitors")
    user.find("Next: exceptions").click()
    user.find("Pick parts").click()
    await user.should_see("No DigiKey API credentials")


async def test_web_basket_padding(user: User, web_page, fake_supplier):
    await user.open("/")
    await _upload(user, (FIXTURES / "wayfinder.csv").read_bytes(), "w.csv")
    await user.should_see("22 capacitors")
    user.find(ui.switch).elements.pop().value = True
    threshold = next(n for n in user.find(ui.number).elements if n.props.get("label", "").startswith("Threshold"))
    threshold.value = 100
    user.find("Next: exceptions").click()
    await user.should_see("Select rows to give them an exception")

    user.find("Pick parts").click()
    await user.should_see("Done — 14 lines searched", retries=40)
    await user.should_see("Basket:")
    user.find("Next: basket padding").click()

    grid = next(g for g in user.find(ui.aggrid).elements if any(c.get("field") == "padded" for c in g.options["columnDefs"]))
    rows = grid.options["rowData"]
    assert len(rows) == 36 and sum(not r["priced"] for r in rows) == 1

    async def selected():
        return [rows[0], rows[1]]

    grid.get_selected_rows = selected  # the simulated user has no browser-side grid
    user.find(kind=ui.button, content="Pad selected").click()  # the help text mentions it too
    await user.should_see("Padded by")
    await user.should_see("✓")
    assert "+" in grid.options["rowData"][0]["padded"]

    user.find("Download CSV").click()
    await _wait(lambda: user.download.http_responses)
    text = user.download.http_responses[-1].content.decode("utf-8-sig")
    assert "padding" in text and "Priced by MPN" in text
