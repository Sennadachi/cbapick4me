"""Custom CSV column mapping: parsing, saved presets, CLI flags, TUI and GUI flows."""

import argparse
import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from textual.widgets import Button, Input, Select, Switch

from cbapick4me.__main__ import build_parser
from cbapick4me.core.bom import BomError, parse_bom, read_table
from cbapick4me.core.config import Config
from cbapick4me.core.formats import CsvFormat, FormatStore, formats_path, load_formats, prefill, save_formats
from cbapick4me.core.parse import Kind
from cbapick4me.core.session import Session
from cbapick4me.core.specs import Settings
from cbapick4me.gui import app as gui
from cbapick4me.headless import format_from_args, run
from cbapick4me.tui.app import CbaPickApp, ColumnsModal, OpenScreen, SetupScreen

from .conftest import FIXTURES

KICAD = CsvFormat({"designator": "Reference", "value": "Value", "footprint": "Footprint", "quantity": "Qty"}, header_row=3)


def kicad_bytes() -> bytes:
    return (FIXTURES / "kicad_bom.csv").read_bytes()


def test_custom_format_parses_kicad_with_preamble():
    bom = parse_bom(kicad_bytes(), "kicad_bom.csv", KICAD)
    assert [r.designators for r in bom.pickable_rows] == [["C1", "C2"], ["C3"], ["R1", "R2", "R3"], ["R4"]]
    c1 = bom.pickable_rows[0]
    assert c1.kind is Kind.CAPACITOR and c1.size == "0603" and c1.value == pytest.approx(100e-9)
    assert bom.rows[-1].designators == ["J1"] and not bom.rows[-1].pickable
    assert bom.columns["designator"] == "Reference"


def test_easyeda_default_cannot_read_kicad_preamble():
    with pytest.raises(BomError, match="designator"):
        parse_bom(kicad_bytes(), "kicad_bom.csv")


def test_semicolon_delimiter_and_renamed_columns():
    data = "Part;Val;Pkg\nC1;1u;0805\nR7;220;0402\n".encode()
    fmt = CsvFormat({"designator": "Part", "value": "Val", "footprint": "Pkg"}, delimiter=";")
    bom = parse_bom(data, "x.csv", fmt)
    assert [(r.designators, r.size) for r in bom.rows] == [(["C1"], "0805"), (["R7"], "0402")]


def test_missing_mapped_header_is_reported():
    fmt = CsvFormat({"designator": "Ref", "value": "Value"}, header_row=3)
    with pytest.raises(BomError, match="'Ref' \\(designator\\) is not in this file"):
        parse_bom(kicad_bytes(), "k.csv", fmt)
    with pytest.raises(BomError, match="value"):
        parse_bom(kicad_bytes(), "k.csv", CsvFormat({"designator": "Reference"}, header_row=3))


def test_header_row_past_end():
    with pytest.raises(BomError, match="past the end"):
        read_table(b"a,b\n1,2\n", header_row=9)


def test_store_round_trip_with_awkward_names():
    store = FormatStore(custom=True, last=KICAD)
    store.presets['My "KiCad" 8'] = KICAD
    store.presets["Altium"] = CsvFormat({"designator": "Designator", "value": "Comment"}, delimiter="\t")
    save_formats(store)
    assert formats_path().exists()
    back = load_formats()
    assert back.custom and back.last == KICAD
    assert back.presets['My "KiCad" 8'] == KICAD
    assert back.presets["Altium"].delimiter == "\t"


def test_broken_store_file_is_ignored():
    formats_path().parent.mkdir(parents=True, exist_ok=True)
    formats_path().write_text("custom = [nope", "utf-8")
    assert load_formats() == FormatStore()


def test_prefill_uses_last_only_when_it_fits():
    headers, _ = read_table(kicad_bytes(), header_row=3)
    assert prefill(headers, FormatStore(last=KICAD)).columns == KICAD.columns
    other = CsvFormat({"designator": "Designator", "value": "Comment"})
    guessed = prefill(headers, FormatStore(last=other)).columns
    assert guessed["designator"] == "Reference" and guessed["mpn"] == "MPN"


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


def test_format_from_args():
    store = FormatStore(presets={"kicad": KICAD})
    assert format_from_args(_args("--headless", "x.csv"), store) is None
    assert format_from_args(_args("--headless", "x.csv", "--preset", "kicad"), store) == KICAD
    fmt = format_from_args(_args("--headless", "x.csv", "--col", "designator=Reference", "--col", "Value=Value", "--header-row", "3"), store)
    assert fmt == CsvFormat({"designator": "Reference", "value": "Value"}, header_row=3)
    with pytest.raises(ValueError, match="No preset called 'nope'"):
        format_from_args(_args("--preset", "nope"), store)
    with pytest.raises(ValueError, match="ROLE=HEADER"):
        format_from_args(_args("--col", "colour=Red"), store)
    # Remembered custom mode: headless uses the last mapping; the UIs ask instead.
    remembered = FormatStore(custom=True, last=KICAD)
    assert format_from_args(_args("--headless", "x.csv"), remembered) == KICAD
    assert format_from_args(_args("x.csv"), remembered) is None
    assert format_from_args(_args("--headless", "x.csv", "--format", "easyeda"), remembered) is None


def test_headless_custom_dry_run(capsys):
    bom = str(FIXTURES / "kicad_bom.csv")
    code = run(_args("--headless", bom, "--dry-run", "--header-row", "3", "--col", "designator=Reference", "--col", "value=Value", "--col", "footprint=Footprint"))
    out = capsys.readouterr().out
    assert code == 0
    assert "4 C/R lines" in out and "CAP CER 100nF 0603 X7R" in out and "RES 4.7k 0805" in out


# --- TUI ----------------------------------------------------------------------


async def test_tui_custom_mapping_and_presets(tmp_path, fake_supplier):
    bom = tmp_path / "kicad_bom.csv"
    bom.write_bytes(kicad_bytes())
    session = Session(Config(cache_dir=str(tmp_path / "cache")), Settings())
    app = CbaPickApp(session)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, OpenScreen)
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.screen.query_one("#custom", Switch).value and load_formats().custom

        app.open_bom(bom)
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ColumnsModal)
        modal.query_one("#header_row", Input).value = "3"
        await pilot.pause()
        # Guessed from the header names once the header row is right.
        assert modal.query_one("#col_designator", Select).value == "Reference"
        assert modal.query_one("#col_footprint", Select).value == "Footprint"
        modal.query_one("#preset_name", Input).value = "KiCad"
        modal.query_one("#preset_save", Button).press()
        await pilot.pause()
        modal.query_one("#use", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        assert len(session.bom.pickable_rows) == 4

    store = load_formats()
    assert store.custom and store.last.header_row == 3 and store.last.columns["designator"] == "Reference"
    assert store.presets["KiCad"] == store.last


async def test_tui_cli_mapping_skips_modal(tmp_path):
    bom = tmp_path / "kicad_bom.csv"
    bom.write_bytes(kicad_bytes())
    session = Session(Config(cache_dir=str(tmp_path / "cache")), Settings())
    session.csv_format = KICAD
    app = CbaPickApp(session, bom, custom=True)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)


# --- GUI ----------------------------------------------------------------------


async def test_web_custom_csv(user: User):
    gui._register(web=True)
    await user.open("/")
    await user.should_see("EasyEDA")
    user.find(ui.toggle).elements.pop().value = "custom"
    await user.should_see("Any EDA tool's CSV")

    from nicegui.elements.upload_files import SmallFileUpload

    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([SmallFileUpload(name="kicad_bom.csv", content_type="text/csv", _data=kicad_bytes())])
    await user.should_see("Columns of kicad_bom.csv")

    user.find(marker="header_row").elements.pop().value = 3
    # Header names are now offered and guessed from the right row.
    for _ in range(100):
        if "Reference" in [s.value for s in user.find(ui.select).elements]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("designator column was not guessed")
    user.find("Use these columns").click()
    await user.should_see("kicad_bom.csv: 3 capacitors, 4 resistors, 2 other lines kept as-is")
    # A hosted site never writes mappings to disk.
    assert not formats_path().exists()
