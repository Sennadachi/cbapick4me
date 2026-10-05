import shutil

from textual.widgets import Checkbox, DataTable, Input, Select

from cbapick4me.core.config import Config
from cbapick4me.core.session import Session
from cbapick4me.core.specs import Settings
from cbapick4me.tui.app import CbaPickApp, OverrideModal, PaddingScreen, ResultsScreen, ReviewScreen, SetupScreen

from .conftest import FIXTURES


async def test_tui_full_flow(tmp_path, fake_supplier):
    bom = tmp_path / "wayfinder.csv"
    shutil.copy(FIXTURES / "wayfinder.csv", bom)
    session = Session(Config(cache_dir=str(tmp_path / "cache")), Settings(boards=2))
    app = CbaPickApp(session, bom)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)

        designators = [r["designator"] for r in session.designator_rows()]
        app.screen.query_one(DataTable).move_cursor(row=designators.index("C9"))
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, OverrideModal)
        app.screen.query_one("#dielectric", Select).value = "X5R"
        await pilot.click("#apply")
        await pilot.pause()
        assert session.settings.overrides == {"C9": {"dielectric": "X5R"}}

        await pilot.press("p")
        assert isinstance(app.screen, ResultsScreen)
        for _ in range(50):
            await pilot.pause(0.05)
            if session.results:
                break
        await pilot.pause(0.2)
        assert len(session.results) == 15

        await pilot.press("s")
        await pilot.pause()

    out = (tmp_path / "wayfinder_picked4u.csv").read_text("utf-8-sig")
    assert "C9," in out and "X5R" in out


async def test_tui_multi_select_same_type_only(tmp_path, fake_supplier):
    bom = tmp_path / "wayfinder.csv"
    shutil.copy(FIXTURES / "wayfinder.csv", bom)
    session = Session(Config(cache_dir=str(tmp_path / "cache")), Settings())
    app = CbaPickApp(session, bom)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+n")
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(DataTable)
        designators = [r["designator"] for r in session.designator_rows()]

        # space marks and moves down; shift+down extends
        await pilot.press("space", "space", "shift+down")
        await pilot.pause()
        assert screen.marked == {"C1", "C2", "C3", "C4"}
        assert [str(table.get_cell(d, "mark")) for d in ("C1", "C4", "C6")] == ["●", "●", ""]
        assert table.columns[screen.mark_col].width >= 1  # zero-width column hid the marks

        # a resistor can't join a capacitor selection
        table.move_cursor(row=designators.index("R1"))
        await pilot.press("space")
        await pilot.pause()
        assert "R1" not in screen.marked

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, OverrideModal)
        app.screen.query_one("#dielectric", Select).value = "C0G"
        app.screen.query_one("#min_voltage").value = "50"
        await pilot.click("#apply")
        await pilot.pause()
        for d in ("C1", "C2", "C3", "C4"):
            assert session.settings.overrides[d] == {"dielectric": "C0G", "min_voltage": 50.0}
        assert not screen.marked

        # "a" marks every part of the cursor row's type
        table.move_cursor(row=designators.index("R1"))
        await pilot.press("a")
        await pilot.pause()
        assert screen.marked == {d for d in designators if d.startswith("R")}


async def test_tui_basket_padding(tmp_path, fake_supplier):
    bom = tmp_path / "wayfinder.csv"
    shutil.copy(FIXTURES / "wayfinder.csv", bom)
    session = Session(Config(cache_dir=str(tmp_path / "cache")), Settings())
    app = CbaPickApp(session, bom)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one("#pad", Checkbox).value = True
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)  # no threshold yet
        app.screen.query_one("#pad_to", Input).value = "100"
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert session.settings.pad_enabled and session.settings.pad_threshold == 100

        await pilot.press("p")
        for _ in range(50):
            await pilot.pause(0.05)
            if session.extras:
                break
        await pilot.pause(0.2)
        assert len(session.extras) == 22

        await pilot.press("ctrl+n")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PaddingScreen)
        table = screen.query_one(DataTable)
        assert table.row_count == len(session.basket())

        # the 50 Ω line has no price and can't be marked
        basket = session.basket()
        r4 = next(i for i, x in enumerate(basket) if "R4" in x.designators)
        table.move_cursor(row=r4)
        await pilot.press("space")
        await pilot.pause()
        assert not screen.marked

        table.move_cursor(row=0)
        await pilot.press("space", "space")
        await pilot.press("p")
        await pilot.pause()
        assert screen.marked == {0, 1}
        assert session.total >= 100 and basket[0].pick.padded and basket[1].pick.padded

        await pilot.press("s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ResultsScreen)

    out = (tmp_path / "wayfinder_picked4u.csv").read_text("utf-8-sig")
    assert "padding" in out and "Priced by MPN" in out
