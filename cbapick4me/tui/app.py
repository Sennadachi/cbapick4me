"""Terminal UI (Textual): open → setup → review exceptions → pick → save."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    ProgressBar,
    Select,
    Static,
    Switch,
)
from textual.widgets.option_list import Option

from ..core.bom import BomError, parse_bom, read_table
from ..core.formats import DELIMITERS, ROLES, CsvFormat, load_formats, prefill, save_formats
from ..core.config import SITE_CURRENCY, Config, load_config, save_config
from ..core.parse import Kind, format_value
from ..core.padding import ALREADY_MET, UNREACHABLE
from ..core.picker import Cancelled, Priced
from ..core.session import Session
from ..core.specs import DIELECTRICS, Spec, coerce_field, field_text
from ..core.suppliers import SupplierAuthError, SupplierError
from ..core.theme import ThemeWatcher

ANY = "__any__"
KEEP = "__keep__"


# --- open ---------------------------------------------------------------------


class BomTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [
            p
            for p in paths
            if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() in (".csv", ".txt", ".tsv"))
        ]


class OpenScreen(Screen):
    BINDINGS = [
        Binding("ctrl+k", "keys", "API keys"),
        Binding("ctrl+t", "toggle_custom", "Custom CSV on/off"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="open"):
            yield Label("Open a BOM (.csv). Type a path or pick from the tree.")
            with Horizontal(id="mode"):
                yield Switch(self.app.custom, id="custom")
                yield Label("Custom CSV columns: off = EasyEDA export, on = choose the columns of any EDA tool's CSV")
            yield Input(placeholder="path/to/BOM.csv", id="path")
            yield BomTree(Path.cwd(), id="tree")
        yield Footer()

    @on(Input.Submitted, "#path")
    def submitted(self, event: Input.Submitted) -> None:
        self.app.open_bom(Path(event.value).expanduser())

    @on(DirectoryTree.FileSelected)
    def selected(self, event: DirectoryTree.FileSelected) -> None:
        self.app.open_bom(event.path)

    def action_keys(self) -> None:
        self.app.push_screen(KeysModal())

    def action_toggle_custom(self) -> None:
        switch = self.query_one("#custom", Switch)
        switch.value = not switch.value

    @on(Switch.Changed, "#custom")
    def custom_changed(self, event: Switch.Changed) -> None:
        self.app.set_custom(event.value)


# --- setup --------------------------------------------------------------------


class SetupScreen(Screen):
    BINDINGS = [
        Binding("ctrl+n", "next", "Review →"),
        Binding("ctrl+k", "keys", "API keys"),
        Binding("ctrl+o", "open", "Open other BOM"),
    ]

    def compose(self) -> ComposeResult:
        s = self.app.session.settings
        bom = self.app.session.bom
        caps = sum(len(r.designators) for r in bom.pickable_rows if r.kind is Kind.CAPACITOR)
        res = sum(len(r.designators) for r in bom.pickable_rows if r.kind is Kind.RESISTOR)
        yield Header()
        with VerticalScroll(id="setup"):
            yield Static(
                f"[b]{bom.name}[/b] — {caps} capacitors, {res} resistors, "
                f"{len(bom.rows) - len(bom.pickable_rows)} other lines (kept as-is)",
                id="bominfo",
            )
            yield Label("General", classes="section")
            with Grid(classes="form"):
                yield Label("Number of boards")
                yield Input(str(s.boards), id="boards", type="integer")
            yield Label("Capacitors (blanket)", classes="section")
            with Grid(classes="form"):
                yield Label("Dielectric")
                yield Select(
                    [(d, d) for d in DIELECTRICS] + [("Any", ANY)],
                    value=s.cap.dielectric or ANY,
                    allow_blank=False,
                    id="dielectric",
                )
                yield Label("Min rated voltage (V)")
                yield Input(field_text("min_voltage", s.cap.min_voltage), id="cap_voltage", placeholder="any")
                yield Label("Max tolerance (%)")
                yield Input(field_text("tolerance", s.cap.tolerance), id="cap_tol", placeholder="any")
            yield Label("Resistors (blanket)", classes="section")
            with Grid(classes="form"):
                yield Label("Min power (W, 1/10W, 100mW)")
                yield Input(field_text("min_power", s.res.min_power), id="res_power", placeholder="any")
                yield Label("Max tolerance (%)")
                yield Input(field_text("tolerance", s.res.tolerance), id="res_tol", placeholder="any")
            yield Label("Pricing", classes="section")
            with Grid(classes="form"):
                yield Label("Add spares when unit price below")
                yield Input(f"{s.cheap_threshold:g}", id="cheap", type="number")
                yield Label("Max extra spend for spares / line")
                yield Input(f"{s.spare_budget:g}", id="budget", type="number")
                yield Label("Require stock ≥ needed ×")
                yield Input(f"{s.stock_factor:g}", id="factor", type="number")
            yield Label("Basket padding", classes="section")
            yield Static(
                "[dim]Prices the rest of the BOM by MPN, then lets you raise quantities on chosen lines "
                "to reach the supplier's free-shipping threshold.[/dim]",
                classes="hint",
            )
            with Grid(classes="form"):
                yield Label("Pad basket to threshold")
                yield Checkbox("", s.pad_enabled, id="pad")
                yield Label("Threshold (supplier currency)")
                yield Input(f"{s.pad_threshold:g}" if s.pad_threshold else "", id="pad_to", type="number", placeholder="e.g. 33")
            with Horizontal(classes="buttons"):
                yield Button("API keys", id="keys")
                yield Button("Review exceptions →", id="next", variant="primary")
        yield Footer()

    def _apply(self) -> bool:
        s = self.app.session.settings
        q = self.query_one
        try:
            s.boards = max(1, int(q("#boards", Input).value or 1))
            diel = q("#dielectric", Select).value
            s.cap = Spec(
                dielectric=None if diel == ANY else diel,
                min_voltage=coerce_field("min_voltage", q("#cap_voltage", Input).value),
                tolerance=coerce_field("tolerance", q("#cap_tol", Input).value),
            )
            s.res = Spec(
                min_power=coerce_field("min_power", q("#res_power", Input).value),
                tolerance=coerce_field("tolerance", q("#res_tol", Input).value),
            )
            s.cheap_threshold = float(q("#cheap", Input).value or 0)
            s.spare_budget = float(q("#budget", Input).value or 0)
            s.stock_factor = float(q("#factor", Input).value or 1)
            s.pad_enabled = q("#pad", Checkbox).value
            s.pad_threshold = float(q("#pad_to", Input).value or 0)
        except (ValueError, ZeroDivisionError) as e:
            self.notify(f"Invalid value: {e}", severity="error")
            return False
        if s.pad_enabled and s.pad_threshold <= 0:
            self.notify("Enter the basket threshold to pad to", severity="error")
            return False
        return True

    @on(Button.Pressed, "#next")
    def action_next(self) -> None:
        if self._apply():
            self.app.push_screen(ReviewScreen())

    @on(Button.Pressed, "#keys")
    def action_keys(self) -> None:
        self.app.push_screen(KeysModal())

    def action_open(self) -> None:
        self.app.switch_screen(OpenScreen())


# --- review -------------------------------------------------------------------


def _cell(text: str, overridden: bool) -> Text:
    return Text(text or "any", style="bold yellow" if overridden else ("dim" if not text else ""))


class ReviewScreen(Screen):
    BINDINGS = [
        Binding("space", "mark", "Mark"),
        Binding("shift+down", "extend(1)", "Mark ↓", show=False),
        Binding("shift+up", "extend(-1)", "Mark ↑", show=False),
        Binding("a", "mark_all", "Mark all of type"),
        Binding("e", "edit", "Edit marked"),
        Binding("r", "reset", "Reset to blanket"),
        Binding("p", "pick", "Pick parts →"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.marked: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Blanket specs apply to every part. Mark rows with [b]space[/b] (or [b]shift+↑/↓[/b]), "
            "then press [b]e[/b] to give them all the same exception (e.g. C9 → X5R). "
            "One type at a time: capacitors or resistors. Yellow = overridden.",
            id="help",
        )
        yield Static("", id="marks")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="review")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        # Fixed width: marks are drawn with update_cell, which doesn't resize columns.
        self.mark_col = t.add_column("", width=1, key="mark")
        t.add_columns("Des", "Value", "Footprint", "Dielectric", "Min V", "Min W", "Tol %")
        self.refresh_table()
        t.focus()

    def refresh_table(self) -> None:
        t = self.query_one(DataTable)
        cursor = t.cursor_row
        t.clear()
        settings = self.app.session.settings
        for r in self.app.session.designator_rows():
            d, spec, kind = r["designator"], r["spec"], r["kind"]
            ov = settings.overrides.get(d, {})
            value = format_value(r["value"], kind) if r["value"] is not None else f"? {r['value_text']}"
            if "value" in ov:
                value = Text(value, style="bold yellow")
            is_cap = kind is Kind.CAPACITOR
            t.add_row(
                "●" if d in self.marked else "",
                d,
                value,
                r["footprint"] if r["size"] else Text(f"? {r['footprint']}", style="red"),
                _cell(spec.dielectric or "", "dielectric" in ov) if is_cap else Text("—", style="dim"),
                _cell(field_text("min_voltage", spec.min_voltage), "min_voltage" in ov) if is_cap else Text("—", style="dim"),
                _cell(field_text("min_power", spec.min_power), "min_power" in ov) if not is_cap else Text("—", style="dim"),
                _cell(field_text("tolerance", spec.tolerance), "tolerance" in ov),
                key=d,
            )
        if t.row_count:
            t.move_cursor(row=min(cursor, t.row_count - 1))
        self._show_marks()

    def _show_marks(self) -> None:
        if not self.marked:
            text = "[dim]Nothing marked — [b]e[/b] edits the row under the cursor.[/dim]"
        else:
            kind = self.app.session.kind_of(next(iter(self.marked)))
            noun = "capacitor" if kind is Kind.CAPACITOR else "resistor"
            names = ", ".join(sorted(self.marked, key=_natural))
            text = f"[b]{len(self.marked)} {noun}{'s' if len(self.marked) != 1 else ''} marked:[/b] {_clip(names, 100)}"
        self.query_one("#marks", Static).update(text)

    def _current(self) -> str | None:
        t = self.query_one(DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key((t.cursor_row, 0)).row_key.value

    def _targets(self) -> list[str]:
        if self.marked:
            return sorted(self.marked, key=_natural)
        cur = self._current()
        return [cur] if cur else []

    def _set_mark(self, d: str, on: bool) -> bool:
        """Mark/unmark one designator; refuses to mix capacitors and resistors."""
        sess = self.app.session
        if on and self.marked and sess.kind_of(d) is not sess.kind_of(next(iter(self.marked))):
            self.notify(
                "Capacitors and resistors can't share an exception — edit one type at a time",
                severity="warning",
            )
            return False
        if on:
            self.marked.add(d)
        else:
            self.marked.discard(d)
        self.query_one(DataTable).update_cell(d, self.mark_col, "●" if on else "")
        return True

    def action_mark(self) -> None:
        d = self._current()
        if d and self._set_mark(d, d not in self.marked):
            t = self.query_one(DataTable)
            t.move_cursor(row=min(t.cursor_row + 1, t.row_count - 1))
        self._show_marks()

    def action_extend(self, delta: int) -> None:
        t = self.query_one(DataTable)
        d = self._current()
        if d and self._set_mark(d, True):
            t.move_cursor(row=max(0, min(t.cursor_row + delta, t.row_count - 1)))
            nxt = self._current()
            if nxt:
                self._set_mark(nxt, True)
        self._show_marks()

    def action_mark_all(self) -> None:
        """Mark every part of the cursor row's type; press again to clear."""
        if self.marked:
            self.marked.clear()
        else:
            d = self._current()
            kind = self.app.session.kind_of(d) if d else None
            self.marked = {r["designator"] for r in self.app.session.designator_rows() if r["kind"] is kind}
        self.refresh_table()

    def action_edit(self) -> None:
        targets = self._targets()
        if not targets:
            return
        kinds = {self.app.session.kind_of(d) for d in targets}
        if len(kinds) > 1:
            self.notify("Marked rows mix capacitors and resistors — edit one type at a time", severity="error")
            return

        def done(changes: dict | None) -> None:
            if not changes:
                return
            try:
                self.app.session.apply_exception(targets, changes)
            except ValueError as e:
                self.notify(str(e), severity="error")
                return
            self.marked.clear()
            self.refresh_table()
            self.notify(f"Updated {_clip(', '.join(targets), 80)}")

        self.app.push_screen(OverrideModal(targets, kinds), done)

    def action_reset(self) -> None:
        for d in self._targets():
            self.app.session.settings.clear_override(d)
        self.marked.clear()
        self.refresh_table()

    def action_pick(self) -> None:
        problem = self.app.session.check_ready()
        if problem:
            self.notify(problem, severity="error")
            if "credentials" in problem:
                self.app.push_screen(KeysModal())
            return
        self.app.push_screen(ResultsScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _natural(d: str) -> tuple:
    import re

    m = re.match(r"([A-Za-z]+)(\d+)", d)
    return (m.group(1), int(m.group(2))) if m else (d, 0)


class OverrideModal(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, designators: list[str], kinds: set[Kind]):
        super().__init__()
        self.designators = designators
        self.kinds = kinds

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            noun = "capacitor" if Kind.CAPACITOR in self.kinds else "resistor"
            count = len(self.designators)
            yield Label(
                f"Exception for {count} {noun}{'s' if count != 1 else ''}: {_clip(', '.join(self.designators), 60)}",
                classes="title",
            )
            yield Static("Leave a field blank to keep it, type [b]any[/b] to remove the requirement.", classes="hint")
            with Grid(classes="form"):
                if Kind.CAPACITOR in self.kinds:
                    yield Label("Dielectric")
                    yield Select(
                        [("(keep)", KEEP)] + [(d, d) for d in DIELECTRICS] + [("Any", ANY)],
                        value=KEEP,
                        allow_blank=False,
                        id="dielectric",
                    )
                    yield Label("Min rated voltage (V)")
                    yield Input(id="min_voltage", placeholder="keep")
                if Kind.RESISTOR in self.kinds:
                    yield Label("Min power (W)")
                    yield Input(id="min_power", placeholder="keep")
                yield Label("Max tolerance (%)")
                yield Input(id="tolerance", placeholder="keep")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    @on(Button.Pressed, "#apply")
    @on(Input.Submitted)
    def apply(self) -> None:
        changes: dict[str, object] = {}
        try:
            for sel in self.query(Select):
                if sel.value != KEEP:
                    changes["dielectric"] = None if sel.value == ANY else sel.value
            for inp in self.query(Input):
                if inp.value.strip():
                    changes[inp.id] = coerce_field(inp.id, inp.value)
        except (ValueError, ZeroDivisionError) as e:
            self.notify(f"Invalid value: {e}", severity="error")
            return
        self.dismiss(changes or None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# --- results ------------------------------------------------------------------


class ResultsScreen(Screen):
    BINDINGS = [
        Binding("enter", "alternatives", "Alternatives", show=True),
        Binding("s", "save", "Save CSV"),
        Binding("n", "nearest", "Use nearest values"),
        Binding("ctrl+n", "padding", "Basket padding →"),
        Binding("escape", "back", "Back to review"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ProgressBar(id="progress", show_eta=False)
        yield Static("", id="status")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="results")
        yield Static("", id="total")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Designators", "Value", "Size", "Spec", "MPN", "Manufacturer", "Supplier PN", "Need", "Order", "Total", "Stock", "Notes")
        self.search()

    @work(thread=True, exclusive=True)
    def search(self) -> None:
        def progress(done: int, total: int, label: str) -> None:
            self.app.call_from_thread(self._progress, done, total, label)

        try:
            self.app.session.run(on_progress=progress)
        except Cancelled:
            return
        except SupplierError as e:
            self.app.call_from_thread(self.notify, str(e), severity="error", timeout=15)
            self.app.call_from_thread(self._status, Text(str(e), style="red"))
            if isinstance(e, SupplierAuthError):
                self.app.call_from_thread(self.app.push_screen, KeysModal())
            return
        self.app.call_from_thread(self.show_results)

    def _progress(self, done: int, total: int, label: str) -> None:
        self.query_one(ProgressBar).update(total=max(total, 1), progress=done)
        self._status(f"Searching DigiKey… {done}/{total} {label}")

    def _status(self, text: str | Text) -> None:
        self.query_one("#status", Static).update(text)

    def show_results(self) -> None:
        sess = self.app.session
        t = self.query_one(DataTable)
        cursor = t.cursor_row
        t.clear()
        for i, r in enumerate(sess.results):
            ln, p = r.line, r.pick
            bad = "red" if not p else ""
            t.add_row(
                Text(_clip(",".join(ln.designators), 26), style=bad),
                format_value(ln.value, ln.kind) if ln.value is not None else ln.row.value_text,
                ln.size or "?",
                ln.spec.describe(ln.kind),
                Text(p.candidate.mpn if p else "NOT PICKED", style=bad or "bold"),
                p.candidate.manufacturer if p else "",
                p.candidate.supplier_part if p else "",
                str(ln.needed),
                str(p.order_qty) if p else "",
                f"{p.line_total:.2f}" if p else "",
                f"{p.candidate.stock:,}" if p else "",
                Text(r.note + _qty_text(p), style="dim" if p else "red"),
                key=str(i),
            )
        if t.row_count:
            t.move_cursor(row=min(cursor, t.row_count - 1))
        t.focus()
        missing = sum(1 for r in sess.results if not r.ok)
        self.query_one("#total", Static).update(
            f"[b]{_basket_text(sess)}[/b]   "
            + (f"[red]{missing} line(s) not picked — Enter for options[/red]   " if missing else "")
            + "[dim]s = save, Enter = alternatives"
            + (", ctrl+n = basket padding" if sess.settings.pad_enabled else "")
            + "[/dim]"
        )
        self._status(f"Done — {len(sess.results)} lines")
        self.query_one(ProgressBar).display = False

    def action_alternatives(self) -> None:
        t = self.query_one(DataTable)
        if not t.row_count or not self.app.session.results:
            return
        idx = t.cursor_row
        r = self.app.session.results[idx]

        def done(choice: tuple | None) -> None:
            if not choice:
                return
            kind, val = choice
            if kind == "pick":
                self.app.session.select(idx, val)
                self.show_results()
            elif kind == "value":
                self.app.session.accept_suggestion(idx, val)
                self.query_one(ProgressBar).display = True
                self.search()

        self.app.push_screen(AlternativesModal(r), done)

    def action_nearest(self) -> None:
        if self.app.session.accept_all_nearest():
            self.query_one(ProgressBar).display = True
            self.search()
        else:
            self.notify("No non-standard values to substitute")

    def action_save(self) -> None:
        if self.app.session.results:
            path = _save(self)
            if path:
                self._status(f"Saved [b]{path}[/b]")

    def action_padding(self) -> None:
        sess = self.app.session
        if not sess.settings.pad_enabled:
            self.notify("Basket padding is off — turn it on in setup", severity="warning")
        elif sess.results:
            self.app.push_screen(PaddingScreen())

    def action_back(self) -> None:
        self.app.pop_screen()


def _qty_text(p) -> str:
    if not p:
        return ""
    return (f" +{p.spares} spares" if p.spares else "") + (f" +{p.padded} padding" if p.padded else "")


def _basket_text(sess: Session) -> str:
    cur = sess.currency
    if not sess.settings.pad_enabled:
        return f"Total: {sess.total:.2f} {cur}"
    text = f"Basket: {sess.total:.2f} / {sess.settings.pad_threshold:.2f} {cur}"
    return text + (f" ({sess.shortfall:.2f} short)" if sess.shortfall else " ✓")


def _save(screen: Screen) -> Path | None:
    try:
        path = screen.app.session.save()
    except OSError as e:
        screen.notify(f"Could not save: {e}", severity="error")
        return None
    screen.notify(f"Saved {path}", timeout=8)
    return path


# --- basket padding -------------------------------------------------------------


class PaddingScreen(Screen):
    BINDINGS = [
        Binding("space", "mark", "Mark"),
        Binding("shift+down", "extend(1)", "Mark ↓", show=False),
        Binding("shift+up", "extend(-1)", "Mark ↑", show=False),
        Binding("a", "mark_all", "Mark all"),
        Binding("p", "pad", "Pad marked"),
        Binding("c", "clear", "Clear padding"),
        Binding("s", "save", "Save CSV"),
        Binding("escape", "back", "Back to results"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.marked: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Mark the lines you'd like more of with [b]space[/b] (or [b]shift+↑/↓[/b]), then press [b]p[/b]. "
            "The shortfall is split as equal extra spend per marked line, using price breaks where they help. "
            "Dim lines have no price and can't be padded.",
            id="help",
        )
        yield Static("", id="marks")
        yield DataTable(cursor_type="row", zebra_stripes=True, id="basket")
        yield Static("", id="total")
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        self.mark_col = t.add_column("", width=1, key="mark")
        t.add_columns("Designators", "Part", "Supplier PN", "Need", "Order", "+Pad", "Unit", "Total", "Notes")
        self.refresh_table()
        t.focus()

    @property
    def basket(self) -> list[Priced]:
        return self.app.session.basket()

    def refresh_table(self, status: str = "") -> None:
        t = self.query_one(DataTable)
        cursor = t.cursor_row
        t.clear()
        for i, x in enumerate(self.basket):
            p = x.pick
            dim = "" if p else "dim"
            part = p.candidate.mpn if p else getattr(x, "query", "") or x.row.value_text
            t.add_row(
                "●" if i in self.marked else "",
                Text(_clip(",".join(x.designators), 26), style=dim),
                Text(_clip(part, 32), style=dim),
                p.candidate.supplier_part if p else "",
                str(x.needed),
                str(p.order_qty) if p else "",
                Text(f"+{p.padded}", style="bold green") if p and p.padded else "",
                f"{p.unit_price:.4g}" if p else "",
                f"{p.line_total:.2f}" if p else "",
                Text(x.note, style="dim" if p else "red"),
                key=str(i),
            )
        if t.row_count:
            t.move_cursor(row=min(cursor, t.row_count - 1))
        sess = self.app.session
        unpriced = sess.unpriced_count
        self.query_one("#total", Static).update(
            f"[b]{_basket_text(sess)}[/b]   "
            + (f"[yellow]{unpriced} line(s) unpriced, counted as 0[/yellow]   " if unpriced else "")
            + status
        )
        self._show_marks()

    def _show_marks(self) -> None:
        if not self.marked:
            text = "[dim]Nothing marked.[/dim]"
        else:
            names = ", ".join(",".join(self.basket[i].designators) for i in sorted(self.marked))
            text = f"[b]{len(self.marked)} line{'s' if len(self.marked) != 1 else ''} marked:[/b] {_clip(names, 100)}"
        self.query_one("#marks", Static).update(text)

    def _current(self) -> int | None:
        t = self.query_one(DataTable)
        return t.cursor_row if t.row_count else None

    def _set_mark(self, i: int, on: bool) -> None:
        if on and not self.basket[i].ok:
            self.notify("That line has no price, so it can't be padded", severity="warning")
            return
        if on:
            self.marked.add(i)
        else:
            self.marked.discard(i)
        self.query_one(DataTable).update_cell(str(i), self.mark_col, "●" if on else "")

    def action_mark(self) -> None:
        i = self._current()
        if i is not None:
            self._set_mark(i, i not in self.marked)
            t = self.query_one(DataTable)
            t.move_cursor(row=min(t.cursor_row + 1, t.row_count - 1))
        self._show_marks()

    def action_extend(self, delta: int) -> None:
        t = self.query_one(DataTable)
        i = self._current()
        if i is not None:
            self._set_mark(i, True)
            t.move_cursor(row=max(0, min(t.cursor_row + delta, t.row_count - 1)))
            self._set_mark(t.cursor_row, True)
        self._show_marks()

    def action_mark_all(self) -> None:
        """Mark every priced line; press again to clear."""
        self.marked = set() if self.marked else {i for i, x in enumerate(self.basket) if x.ok}
        self.refresh_table()

    def action_pad(self) -> None:
        sess = self.app.session
        if not self.marked:
            self.notify("Mark the lines to increase first", severity="warning")
            return
        outcome = sess.pad(sorted(self.marked))
        cur = sess.currency
        if outcome.status == ALREADY_MET:
            status = "[green]Threshold already met — nothing to pad[/green]"
        elif outcome.status == UNREACHABLE:
            status = f"[red]Stock limits stop these lines reaching the threshold — padded by {outcome.added:.2f} {cur}; mark more lines[/red]"
        else:
            status = f"[green]Padded by {outcome.added:.2f} {cur} across {len(self.marked)} line(s)[/green]"
        self.refresh_table(status)

    def action_clear(self) -> None:
        self.app.session.clear_padding()
        self.refresh_table("Padding cleared")

    def action_save(self) -> None:
        path = _save(self)
        if path:
            self.refresh_table(f"Saved [b]{path}[/b]")

    def action_back(self) -> None:
        self.app.pop_screen()
        if isinstance(self.app.screen, ResultsScreen):
            self.app.screen.show_results()  # padding changed quantities and totals


class AlternativesModal(ModalScreen[tuple | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, result) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        r = self.result
        opts: list[Option] = []
        for i, p in enumerate(r.picks):
            c = p.candidate
            mark = "✓ " if i == r.selected else "  "
            opts.append(
                Option(
                    f"{mark}{c.mpn}  ({c.manufacturer})  {c.supplier_part}  {c.packaging}\n"
                    f"    order {p.order_qty} @ {p.unit_price:.4g} = {p.line_total:.2f}   stock {c.stock:,}",
                    id=f"pick:{i}",
                )
            )
        for v in r.suggestions:
            opts.append(Option(f"↻ Use {format_value(v, r.line.kind)} instead and search again", id=f"value:{v}"))
        with Vertical(classes="modal wide"):
            yield Label(f"{', '.join(r.line.designators)} — {r.line.spec.describe(r.line.kind)}", classes="title")
            if r.note:
                yield Static(r.note, classes="hint")
            if opts:
                yield OptionList(*opts, id="alts")
            else:
                yield Static("No alternatives. Go back and relax the spec for these designators.")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        kind, _, val = event.option.id.partition(":")
        self.dismiss((kind, int(val) if kind == "pick" else float(val)))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# --- API keys -----------------------------------------------------------------


class ColumnsModal(ModalScreen[CsvFormat | None]):
    """Map a custom CSV's headers to roles, with a preview and saved presets."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, path: Path, data: bytes):
        super().__init__()
        self.path = path
        self.data = data
        self.headers: list[str] = []
        self.rows: list[list[str]] = []

    def compose(self) -> ComposeResult:
        store = self.app.formats
        start = store.last or CsvFormat()
        delim = next((k for k, v in DELIMITERS.items() if v == start.delimiter), "auto")
        with VerticalScroll(classes="modal wide"):
            yield Label(f"Custom CSV columns — {self.path.name}", classes="title")
            yield Static(
                "Choose which column of your file holds each item. Designator and Value are required; "
                "Footprint is needed to know the chip size (0603, C_0805_2012Metric…).",
                classes="hint",
            )
            with Horizontal(classes="presets"):
                yield Select([(n, n) for n in store.presets], prompt="Presets", id="preset")
                yield Button("Load", id="preset_load")
                yield Input(placeholder="preset name", id="preset_name")
                yield Button("Save preset", id="preset_save")
                yield Button("Delete", id="preset_delete")
            with Grid(classes="form"):
                yield Label("Delimiter")
                yield Select([(k.capitalize(), k) for k in DELIMITERS], value=delim, allow_blank=False, id="delimiter")
                yield Label("Header row (line number)")
                yield Input(str(start.header_row), id="header_row", type="integer")
                for role, label, required in ROLES:
                    yield Label(f"{label} *" if required else label)
                    yield Select([], prompt="— not used —", id=f"col_{role}")
            yield Label("Preview", classes="section")
            yield DataTable(id="preview", cursor_type="none")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Use columns", id="use", variant="primary")

    def on_mount(self) -> None:
        self.reread()
        self.set_columns(prefill(self.headers, self.app.formats, self.layout_format()).columns)

    # --- form <-> CsvFormat

    def layout_format(self) -> CsvFormat:
        try:
            header_row = max(1, int(self.query_one("#header_row", Input).value or 1))
        except ValueError:
            header_row = 1
        return CsvFormat({}, DELIMITERS[self.query_one("#delimiter", Select).value], header_row)

    def form_format(self) -> CsvFormat:
        fmt = self.layout_format()
        for role, _, _ in ROLES:
            sel = self.query_one(f"#col_{role}", Select)
            if not sel.is_blank():
                fmt.columns[role] = str(sel.value)
        return fmt

    def set_columns(self, columns: dict[str, str]) -> None:
        for role, _, _ in ROLES:
            sel = self.query_one(f"#col_{role}", Select)
            h = columns.get(role)
            if h in self.headers:
                sel.value = h
            else:
                sel.clear()
        self.refresh_preview()

    def reread(self) -> None:
        """Re-read the headers after the delimiter or header row changed."""
        fmt = self.layout_format()
        try:
            self.headers, self.rows = read_table(self.data, fmt.delimiter, fmt.header_row)
        except (BomError, UnicodeError) as e:
            self.headers, self.rows = [], []
            self.notify(str(e), severity="warning")
        for role, _, _ in ROLES:
            sel = self.query_one(f"#col_{role}", Select)
            old = None if sel.is_blank() else sel.value
            sel.set_options([(h or f"(column {i + 1})", h) for i, h in enumerate(self.headers)])
            if old in self.headers:
                sel.value = old

    def refresh_preview(self) -> None:
        fmt = self.form_format()
        t = self.query_one("#preview", DataTable)
        t.clear(columns=True)
        used = [(label, self.headers.index(fmt.columns[role])) for role, label, _ in ROLES if role in fmt.columns]
        if not used:
            return
        t.add_columns(*(label for label, _ in used))
        for raw in self.rows[:5]:
            t.add_row(*(raw[i] if i < len(raw) else "" for _, i in used))

    # --- events

    @on(Select.Changed, "#delimiter")
    @on(Input.Changed, "#header_row")
    def layout_changed(self) -> None:
        self.reread()
        if not self.form_format().columns:  # e.g. the header row was wrong until now: guess afresh
            self.set_columns(prefill(self.headers, self.app.formats, self.layout_format()).columns)
        self.refresh_preview()

    @on(Select.Changed)
    def column_changed(self, event: Select.Changed) -> None:
        if event.select.id and event.select.id.startswith("col_"):
            self.refresh_preview()

    def _apply(self, fmt: CsvFormat) -> None:
        self.query_one("#delimiter", Select).value = next((k for k, v in DELIMITERS.items() if v == fmt.delimiter), "auto")
        self.query_one("#header_row", Input).value = str(fmt.header_row)
        self.reread()
        self.set_columns(fmt.columns)

    @on(Button.Pressed, "#preset_load")
    def preset_load(self) -> None:
        sel = self.query_one("#preset", Select)
        if sel.is_blank():
            self.notify("Choose a preset first", severity="warning")
            return
        self._apply(self.app.formats.presets[sel.value])
        self.query_one("#preset_name", Input).value = str(sel.value)

    @on(Button.Pressed, "#preset_save")
    def preset_save(self) -> None:
        name = self.query_one("#preset_name", Input).value.strip()
        if not name:
            self.notify("Type a name for the preset", severity="warning")
            return
        store = self.app.formats
        store.presets[name] = self.form_format()
        save_formats(store)
        self.query_one("#preset", Select).set_options([(n, n) for n in store.presets])
        self.query_one("#preset", Select).value = name
        self.notify(f"Saved preset '{name}'")

    @on(Button.Pressed, "#preset_delete")
    def preset_delete(self) -> None:
        sel = self.query_one("#preset", Select)
        if sel.is_blank():
            return
        name = str(sel.value)
        store = self.app.formats
        store.presets.pop(name, None)
        save_formats(store)
        sel.set_options([(n, n) for n in store.presets])
        self.notify(f"Deleted preset '{name}'")

    @on(Button.Pressed, "#use")
    def use(self) -> None:
        fmt = self.form_format()
        try:
            parse_bom(self.data, self.path.name, fmt)
        except BomError as e:
            self.notify(str(e), severity="error")
            return
        self.dismiss(fmt)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class KeysModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        cfg = load_config(use_env=False)
        site = cfg.digikey_site.upper() if cfg.digikey_site.upper() in SITE_CURRENCY else "US"
        with Vertical(classes="modal"):
            yield Label("API keys", classes="title")
            yield Static(
                "DigiKey: on developer.digikey.com open your Production app and copy its Client ID "
                "and Client Secret (long strings of letters and digits, not the app name). "
                "Environment variables override these.",
                classes="hint",
            )
            with Grid(classes="form"):
                yield Label("DigiKey Client ID")
                yield Input(cfg.digikey_client_id, id="digikey_client_id")
                yield Label("DigiKey Client Secret")
                yield Input(cfg.digikey_client_secret, id="digikey_client_secret", password=True)
                yield Label("DigiKey site")
                yield Select([(f"{k} ({v})", k) for k, v in SITE_CURRENCY.items()], value=site, allow_blank=False, id="digikey_site")
                yield Label("Currency (blank = site's)")
                yield Input(cfg.currency, id="currency", placeholder=SITE_CURRENCY[site])
            yield Static("", id="test_result")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Test DigiKey", id="test_digikey")
                yield Button("Save", id="save", variant="primary")

    def _form_config(self) -> Config:
        cfg = load_config(use_env=False)
        for inp in self.query(Input):
            setattr(cfg, inp.id, inp.value.strip())
        cfg.digikey_site = self.query_one("#digikey_site", Select).value
        cfg.currency = cfg.currency.upper()
        return cfg

    @on(Select.Changed, "#digikey_site")
    def site_changed(self, event: Select.Changed) -> None:
        self.query_one("#currency", Input).placeholder = SITE_CURRENCY.get(str(event.value), "USD")

    @on(Button.Pressed, "#test_digikey")
    def test_digikey(self) -> None:
        self._check_keys(self._form_config())

    @work(thread=True, exclusive=True, group="keytest")
    def _check_keys(self, cfg: Config) -> None:
        out = self.query_one("#test_result", Static)
        self.app.call_from_thread(out.update, "Testing DigiKey…")
        try:
            msg = Text(f"✓ {Session(cfg).test_keys()}", style="green")
        except SupplierError as e:
            msg = Text(f"✗ {e}", style="red")
        self.app.call_from_thread(out.update, msg)

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        path = save_config(self._form_config())
        self.app.session.cfg = load_config()
        self.notify(f"Saved to {path}")
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# --- app ----------------------------------------------------------------------


class CbaPickApp(App):
    TITLE = "cbapick4me"
    SUB_TITLE = "BOM part picker for EasyEDA & more"
    CSS = """
    #open { padding: 1 2; }
    #tree { height: 1fr; margin-top: 1; }
    #mode { height: auto; margin: 1 0; }
    #mode Label { padding: 1 1; }
    .presets { height: auto; margin-bottom: 1; }
    .presets Select { width: 30; }
    .presets Input { width: 24; }
    #preview { height: auto; max-height: 9; }
    #setup { padding: 1 2; }
    #bominfo { margin-bottom: 1; }
    .section { text-style: bold; color: $accent; margin-top: 1; }
    .form { grid-size: 2; grid-columns: 36 1fr; grid-rows: auto; grid-gutter: 0 1; height: auto; }
    .form Label { padding: 1 0 0 0; }
    .buttons { height: auto; margin-top: 1; align-horizontal: right; }
    .buttons Button { margin-left: 1; }
    #help, #marks, #status, #total { padding: 0 1; height: auto; }
    #test_result { height: auto; margin-top: 1; }
    #review, #results, #basket { height: 1fr; }
    #progress { padding: 0 1; }
    ModalScreen { align: center middle; }
    .modal { width: 80; height: auto; max-height: 90%; border: thick $accent; background: $surface; padding: 1 2; }
    .modal.wide { width: 110; }
    .modal .form { grid-columns: 28 1fr; }
    .title { text-style: bold; margin-bottom: 1; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #alts { height: auto; max-height: 24; }
    """

    def __init__(self, session: Session, start: Path | None = None, custom: bool | None = None):
        super().__init__()
        self.session = session
        self.start = start
        self.formats = load_formats()
        # Custom CSV mode: ask for the columns when opening, unless the command line already set them.
        self.custom = self.formats.custom if custom is None else custom
        self.theme_watcher = ThemeWatcher()
        self._theme_serial = 0

    def on_mount(self) -> None:
        self.follow_desktop_theme()
        self.set_interval(2.0, self.follow_desktop_theme)
        if self.start and self.needs_mapping():
            self.push_screen(OpenScreen())
            self.open_bom(self.start)
        elif self.start:
            if not self.open_bom(self.start):
                self.push_screen(OpenScreen())
        else:
            self.push_screen(OpenScreen())

    def follow_desktop_theme(self) -> None:
        """Restyle to the current Omarchy theme whenever it changes."""
        p = self.theme_watcher.changed()
        if p is None:
            return
        old = self.theme if self.theme.startswith("omarchy-") else None
        self._theme_serial += 1
        name = f"omarchy-{self._theme_serial}"
        self.register_theme(
            Theme(
                name=name,
                primary=p.accent,
                secondary=p.magenta,
                accent=p.cyan,
                warning=p.yellow,
                error=p.red,
                success=p.green,
                foreground=p.foreground,
                background=p.background,
                surface=p.surface,
                panel=p.panel,
                dark=p.dark,
                variables={
                    "input-selection-background": f"{p.selection} 70%",
                    "footer-key-foreground": p.accent,
                    "block-cursor-text-style": "bold",
                },
            )
        )
        self.theme = name
        if old:
            self.unregister_theme(old)

    def set_custom(self, on: bool) -> None:
        self.custom = on
        self.formats.custom = on
        save_formats(self.formats)

    def needs_mapping(self) -> bool:
        return self.custom and self.session.csv_format is None

    def open_bom(self, path: Path) -> bool:
        """Open a BOM; in custom CSV mode ask for its columns first (returns False if that's pending)."""
        if self.needs_mapping():
            try:
                data = path.read_bytes()
            except OSError as e:
                self.notify(f"Could not open {path}: {e}", severity="error")
                return False

            def mapped(fmt: CsvFormat | None) -> None:
                if fmt is None:
                    return
                self.formats.last = fmt
                self.formats.custom = True
                save_formats(self.formats)
                self.session.csv_format = fmt
                try:
                    self.open_bom(path)
                finally:
                    # Ask again for the next BOM, pre-filled with this mapping.
                    self.session.csv_format = None

            self.push_screen(ColumnsModal(path, data), mapped)
            return False
        if not self.custom:
            self.session.csv_format = None
        try:
            self.session.load_path(path)
        except (OSError, BomError, UnicodeError) as e:
            self.notify(f"Could not open {path}: {e}", severity="error")
            return False
        if not self.session.bom.pickable_rows:
            self.notify("No capacitors (C#) or resistors (R#) found in that BOM", severity="warning")
            return False
        if isinstance(self.screen, OpenScreen):
            self.switch_screen(SetupScreen())
        else:
            self.push_screen(SetupScreen())
        return True


def run_tui(a: argparse.Namespace) -> int:
    from ..headless import apply_spec_args, format_from_args, wants_custom

    session = Session()
    apply_spec_args(session, a)
    store = load_formats()
    session.csv_format = format_from_args(a, store)
    CbaPickApp(session, Path(a.bom) if a.bom else None, custom=wants_custom(a, store)).run()
    return 0
