"""NiceGUI front end. Runs as a native desktop window (Windows default) or as a website.

Web mode differences:
  * the BOM is uploaded and parsed in memory; the result is a browser download
  * API keys come from the server environment; a user may enter their own keys,
    which live only in that tab's memory (never written to disk or logged)
  * per-IP rate limiting, upload size and BOM row limits
All state is created inside the page function, so every browser tab is isolated.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path

from nicegui import app, native, run, ui

from ..core.bom import BomError
from ..core.config import SITE_CURRENCY, Config, load_config, save_config
from ..core.padding import ALREADY_MET, UNREACHABLE
from ..core.parse import Kind, format_value
from ..core.picker import LineResult
from ..core.session import Session
from ..core.specs import DIELECTRICS, Spec, coerce_field, field_text
from ..core.theme import ThemeWatcher
from ..core.suppliers import SupplierAuthError, SupplierError

ANY = "any"
KEEP = "(keep)"

MAX_UPLOAD = 1_000_000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class RateLimiter:
    """At most `limit` pick runs per `window` seconds per client IP (web mode)."""

    def __init__(self, limit: int, window: float = 3600):
        self.limit = limit
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


CSS = """
.ov { background: color-mix(in srgb, var(--cba-yellow, #facc15) 35%, transparent) !important; font-weight: 600; }
.bad { color: var(--cba-red, #dc2626) !important; }
.ag-theme-balham, .ag-theme-quartz { --ag-font-size: 13px; }

/* Desktop theme (Omarchy): only active once body has .cba-themed */
body.cba-themed { background: var(--cba-bg) !important; color: var(--cba-fg) !important; }
body.cba-themed .q-header { background: var(--cba-panel) !important; color: var(--cba-fg) !important; }
body.cba-themed .q-header .q-btn { color: var(--cba-accent) !important; }
body.cba-themed .q-card, body.cba-themed .q-stepper, body.cba-themed .q-menu,
body.cba-themed .q-uploader, body.cba-themed .q-uploader__header {
  background: var(--cba-surface) !important; color: var(--cba-fg) !important;
}
body.cba-themed .q-field__native, body.cba-themed .q-field__label, body.cba-themed .q-item,
body.cba-themed .q-stepper__title { color: var(--cba-fg) !important; }
body.cba-themed .q-item--active, body.cba-themed .q-item.q-manual-focusable--focused { color: var(--cba-accent) !important; }
/* AG Grid 34 theming API: params live on the .ag-theme-params-N wrapper, so out-specify it. */
body.cba-themed .nicegui-aggrid [class*="ag-theme-params-"] {
  --ag-background-color: var(--cba-bg);
  --ag-foreground-color: var(--cba-fg);
  --ag-text-color: var(--cba-fg);
  --ag-accent-color: var(--cba-accent);
  --ag-chrome-background-color: var(--cba-panel);
  --ag-header-background-color: var(--cba-panel);
  --ag-header-text-color: var(--cba-fg);
  --ag-subtle-text-color: var(--cba-muted);
  --ag-odd-row-background-color: var(--cba-surface);
  --ag-row-hover-color: color-mix(in srgb, var(--cba-selection) 70%, transparent);
  --ag-selected-row-background-color: color-mix(in srgb, var(--cba-accent) 28%, transparent);
  --ag-border-color: var(--cba-selection);
  --ag-row-border: solid 1px var(--cba-selection);
  --ag-checkbox-checked-background-color: var(--cba-accent);
  --ag-checkbox-checked-border-color: var(--cba-accent);
  --ag-checkbox-unchecked-border-color: var(--cba-muted);
  --ag-tooltip-background-color: var(--cba-panel);
}
"""


def build_page(web: bool, limiter: RateLimiter | None, max_rows: int) -> None:
    cfg = load_config(use_file=not web)
    session = Session(cfg)
    s = session.settings
    progress = {"done": 0, "total": 0, "label": "", "running": False, "error": None, "finished": False}
    client_ip = ""
    try:
        from nicegui import context

        req = context.client.request
        client_ip = (req.headers.get("x-forwarded-for", "").split(",")[0].strip() or req.client.host) if req else ""
    except Exception:
        pass

    ui.add_css(CSS)
    ui.page_title("cbapick4me")

    # ---------------------------------------------------------------- desktop theme
    # Follow the local Omarchy theme in the desktop window; a hosted site keeps its default look.
    if not web:
        dark_mode = ui.dark_mode()
        watcher = ThemeWatcher()

        def follow_desktop_theme() -> None:
            p = watcher.changed()
            if p is None:
                return
            dark_mode.value = p.dark
            ui.run_javascript(
                f"""
                const vars = {json.dumps(p.css_vars())};
                // NiceGUI sets Quasar's --q-* on <body>, so ours must go there too.
                for (const [k, v] of Object.entries(vars)) document.body.style.setProperty(k, v);
                document.body.classList.add('cba-themed');
                """
            )

        ui.timer(2.0, follow_desktop_theme)
        ui.timer(0.1, follow_desktop_theme, once=True)

    # ---------------------------------------------------------------- header
    with ui.header().classes("items-center justify-between"):
        ui.label("cbapick4me").classes("text-xl font-bold")
        ui.label("EasyEDA BOM part picker").classes("opacity-80")
        ui.space()
        ui.button("API keys", icon="key", on_click=lambda: keys_dialog.open()).props("flat color=white")

    # ---------------------------------------------------------------- API keys dialog
    with ui.dialog() as keys_dialog, ui.card().classes("w-[36rem] max-w-full"):
        ui.label("API keys").classes("text-lg font-bold")
        if web:
            ui.markdown(
                "This server may already have keys configured. You can use **your own** instead: "
                "they stay in this browser tab's memory and are never stored on the server."
            ).classes("text-sm")
        else:
            ui.markdown(
                "DigiKey: create a Production app at [developer.digikey.com](https://developer.digikey.com) "
                "with *Product Information v4*."
            ).classes("text-sm")
        file_cfg = Config() if web else load_config(use_env=False)
        ui.label(
            "Copy the Client ID and Client Secret from your Production app's page — "
            "long strings of letters and digits, not the app name."
        ).classes("text-xs opacity-70")
        k_id = ui.input("DigiKey Client ID", value=file_cfg.digikey_client_id).classes("w-full")
        k_secret = ui.input("DigiKey Client Secret", value=file_cfg.digikey_client_secret, password=True, password_toggle_button=True).classes("w-full")
        site = cfg.digikey_site.upper() if cfg.digikey_site.upper() in SITE_CURRENCY else "US"
        with ui.row().classes("w-full"):
            k_site = ui.select({k: f"{k} ({v})" for k, v in SITE_CURRENCY.items()}, value=site, label="DigiKey site").classes("w-36")
            k_cur = ui.input("Currency", value=cfg.currency, placeholder=SITE_CURRENCY[site]).classes("w-36")
            k_site.on_value_change(lambda e: k_cur.props(f'placeholder="{SITE_CURRENCY.get(e.value, "USD")}"'))
        ui.label("Leave currency blank to use the site's own (UK → GBP).").classes("text-xs opacity-70")
        test_result = ui.label("").classes("text-sm")

        def form_updates() -> dict[str, str]:
            return {
                "digikey_client_id": k_id.value.strip(),
                "digikey_client_secret": k_secret.value.strip(),
                "digikey_site": k_site.value or "US",
                "currency": (k_cur.value or "").strip().upper(),
            }

        def form_config() -> Config:
            updates = form_updates()
            if web:
                # Tab-local only: overlay on the server's env config, skipping blank keys.
                base = load_config(use_file=False)
                return replace(base, **{k: v for k, v in updates.items() if v or k == "currency"})
            return replace(load_config(use_env=False), **updates)

        async def test_keys() -> None:
            test_result.classes(remove="text-negative text-positive")
            test_result.text = "Testing DigiKey…"
            try:
                msg = await run.io_bound(Session(form_config()).test_keys)
                test_result.text = f"✓ {msg}"
                test_result.classes(add="text-positive")
            except SupplierError as e:
                test_result.text = f"✗ {e}"
                test_result.classes(add="text-negative")

        def save_keys() -> None:
            if web:
                session.cfg = form_config()
                ui.notify("Keys set for this tab only")
            else:
                path = save_config(form_config())
                session.cfg = load_config()
                ui.notify(f"Saved to {path}")
            keys_dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=keys_dialog.close).props("flat")
            ui.button("Test DigiKey", on_click=test_keys).props("outline")
            ui.button("Save", on_click=save_keys)

    # ---------------------------------------------------------------- stepper
    with ui.column().classes("w-full max-w-7xl mx-auto p-2 sm:p-4"):
        with ui.stepper().props("vertical animated").classes("w-full") as stepper:
            # ---- step 1: BOM
            with ui.step("BOM"):
                ui.label("Export the BOM from EasyEDA (Standard or Pro) and open it here.")
                bom_info = ui.label("").classes("font-medium")

                async def on_upload(e) -> None:
                    try:
                        data = await e.file.read()
                        load(data, e.file.name)
                    except Exception as ex:  # parsing errors shown to user
                        ui.notify(f"Could not read BOM: {ex}", type="negative")
                    finally:
                        upload.reset()

                def load(data: bytes, name: str, path: Path | None = None) -> None:
                    if path:
                        session.load_path(path)
                    else:
                        session.load_bytes(data, name)
                    bom = session.bom
                    if web and len(bom.rows) > max_rows:
                        session.bom = None
                        raise BomError(f"BOM has {len(bom.rows)} rows; this server allows {max_rows}")
                    if not bom.pickable_rows:
                        raise BomError("No capacitors (C#) or resistors (R#) found")
                    caps = sum(len(r.designators) for r in bom.pickable_rows if r.kind is Kind.CAPACITOR)
                    res = sum(len(r.designators) for r in bom.pickable_rows if r.kind is Kind.RESISTOR)
                    bom_info.text = (
                        f"{bom.name}: {caps} capacitors, {res} resistors, "
                        f"{len(bom.rows) - len(bom.pickable_rows)} other lines kept as-is"
                    )
                    refresh_review()
                    stepper.next()

                upload = ui.upload(
                    label="Drop BOM .csv here or click +",
                    auto_upload=True,
                    max_file_size=MAX_UPLOAD,
                    on_upload=on_upload,
                    on_rejected=lambda: ui.notify("File too large (max 1 MB)", type="negative"),
                ).props('accept=".csv,.txt,.tsv"').classes("w-full max-w-xl")

                if not web and app.native.main_window is not None:

                    async def native_open() -> None:
                        import webview

                        files = await app.native.main_window.create_file_dialog(
                            webview.FileDialog.OPEN, file_types=("BOM files (*.csv;*.txt;*.tsv)", "All files (*.*)")
                        )
                        if files:
                            try:
                                load(b"", "", Path(files[0]))
                            except Exception as ex:
                                ui.notify(f"Could not read BOM: {ex}", type="negative")

                    ui.button("Open file…", icon="folder_open", on_click=native_open)

            # ---- step 2: specs
            with ui.step("Specs"):
                with ui.grid(columns="repeat(auto-fit, minmax(16rem, 1fr))").classes("w-full gap-4"):
                    with ui.card():
                        ui.label("General").classes("font-bold")
                        in_boards = ui.number("Number of boards", value=s.boards, min=1, step=1, format="%d").classes("w-full")
                    with ui.card():
                        ui.label("Capacitors (blanket)").classes("font-bold")
                        sel_diel = ui.select(DIELECTRICS + [ANY], value=s.cap.dielectric or ANY, label="Dielectric").classes("w-full")
                        in_cv = ui.input("Min rated voltage (V)", value=field_text("min_voltage", s.cap.min_voltage), placeholder="any").classes("w-full")
                        in_ct = ui.input("Max tolerance (%)", value=field_text("tolerance", s.cap.tolerance), placeholder="any").classes("w-full")
                    with ui.card():
                        ui.label("Resistors (blanket)").classes("font-bold")
                        in_rp = ui.input("Min power (W, 1/10W, 100mW)", value=field_text("min_power", s.res.min_power), placeholder="any").classes("w-full")
                        in_rt = ui.input("Max tolerance (%)", value=field_text("tolerance", s.res.tolerance), placeholder="any").classes("w-full")
                    with ui.card():
                        ui.label("Pricing & spares").classes("font-bold")
                        in_cheap = ui.number("Add spares when unit price below", value=s.cheap_threshold, min=0, step=0.01).classes("w-full")
                        in_budget = ui.number("Max extra spend for spares / line", value=s.spare_budget, min=0, step=0.5).classes("w-full")
                        in_factor = ui.number("Require stock ≥ needed ×", value=s.stock_factor, min=1, step=1).classes("w-full")
                    with ui.card():
                        ui.label("Basket padding").classes("font-bold")
                        sw_pad = ui.switch("Pad basket to free-shipping threshold", value=s.pad_enabled)
                        in_pad_to = ui.number("Threshold (supplier currency)", value=s.pad_threshold or None, min=0, step=1).classes("w-full")
                        in_pad_to.bind_visibility_from(sw_pad, "value")
                        ui.label("Prices the rest of the BOM by MPN, then lets you raise quantities on chosen lines.").classes(
                            "text-xs opacity-70"
                        )

                def apply_specs() -> bool:
                    try:
                        s.boards = max(1, int(in_boards.value or 1))
                        s.cap = Spec(
                            dielectric=None if sel_diel.value == ANY else sel_diel.value,
                            min_voltage=coerce_field("min_voltage", in_cv.value or ""),
                            tolerance=coerce_field("tolerance", in_ct.value or ""),
                        )
                        s.res = Spec(
                            min_power=coerce_field("min_power", in_rp.value or ""),
                            tolerance=coerce_field("tolerance", in_rt.value or ""),
                        )
                        s.cheap_threshold = float(in_cheap.value or 0)
                        s.spare_budget = float(in_budget.value or 0)
                        s.stock_factor = float(in_factor.value or 1)
                        s.pad_enabled = bool(sw_pad.value)
                        s.pad_threshold = float(in_pad_to.value or 0)
                    except (ValueError, ZeroDivisionError) as e:
                        ui.notify(f"Invalid value: {e}", type="negative")
                        return False
                    if s.pad_enabled and s.pad_threshold <= 0:
                        ui.notify("Enter the basket threshold to pad to", type="negative")
                        return False
                    refresh_review()
                    return True

                with ui.stepper_navigation():
                    ui.button("Back", on_click=stepper.previous).props("flat")
                    ui.button("Next: exceptions", on_click=lambda: apply_specs() and stepper.next())

            # ---- step 3: exceptions
            with ui.step("Exceptions"):
                ui.markdown(
                    "Every part uses the blanket specs. Tick rows and apply an exception "
                    "(e.g. **C9 → X5R**). Highlighted cells are overridden."
                ).classes("text-sm")
                review = ui.aggrid(
                    {
                        "columnDefs": [
                            {"field": "designator", "headerName": "Des", "width": 120, "pinned": "left"},
                            {"field": "value", "headerName": "Value", "width": 110, "cellClassRules": {"ov": "data._ov_value"}},
                            {"field": "footprint", "headerName": "Footprint", "width": 120, "cellClassRules": {"bad": "!data.size"}},
                            {"field": "dielectric", "headerName": "Dielectric", "width": 110, "cellClassRules": {"ov": "data._ov_dielectric"}},
                            {"field": "min_voltage", "headerName": "Min V", "width": 90, "cellClassRules": {"ov": "data._ov_min_voltage"}},
                            {"field": "min_power", "headerName": "Min W", "width": 90, "cellClassRules": {"ov": "data._ov_min_power"}},
                            {"field": "tolerance", "headerName": "Tol %", "width": 90, "cellClassRules": {"ov": "data._ov_tolerance"}},
                        ],
                        "rowData": [],
                        # No header checkbox: "select all" would mix capacitors and resistors.
                        "rowSelection": {"mode": "multiRow", "checkboxes": True, "headerCheckbox": False, "enableClickSelection": True},
                        "selectionColumnDef": {"pinned": "left", "width": 44},
                        ":getRowId": "(params) => params.data.designator",
                    },
                    theme="balham",
                ).classes("w-full h-96")
                ui.label("Tip: Ctrl/Shift-click or tick boxes to select several rows of the same type.").classes("text-xs opacity-70")

                with ui.row().classes("gap-2"):
                    ui.button("Select all capacitors", on_click=lambda: select_kind(Kind.CAPACITOR)).props("outline dense")
                    ui.button("Select all resistors", on_click=lambda: select_kind(Kind.RESISTOR)).props("outline dense")
                    ui.button("Clear selection", on_click=lambda: review.run_grid_method("deselectAll")).props("flat dense")

                with ui.card().classes("w-full"):
                    sel_label = ui.label("Select rows to give them an exception").classes("font-medium")
                    ui.label("Blank keeps the current value, 'any' removes the requirement").classes("text-xs opacity-70")
                    with ui.row().classes("items-end gap-4 flex-wrap"):
                        ov_diel = ui.select([KEEP] + DIELECTRICS + [ANY], value=KEEP, label="Dielectric").classes("w-32")
                        ov_v = ui.input("Min V").classes("w-24")
                        ov_w = ui.input("Min W").classes("w-24")
                        ov_t = ui.input("Max tol %").classes("w-24")
                        apply_btn = ui.button("Apply", icon="edit", on_click=lambda: apply_override())
                        reset_btn = ui.button("Reset to blanket", icon="undo", on_click=lambda: reset_override()).props("flat")
                cap_only, res_only = (ov_diel, ov_v), (ov_w,)

                async def selected_designators() -> list[str]:
                    rows = await review.get_selected_rows()
                    return [r["designator"] for r in rows]

                def show_selection(targets: list[str]) -> None:
                    kinds = {session.kind_of(d) for d in targets}
                    apply_btn.set_enabled(len(kinds) == 1)
                    reset_btn.set_enabled(bool(targets))
                    for el in cap_only:
                        el.set_enabled(kinds != {Kind.RESISTOR})
                    for el in res_only:
                        el.set_enabled(kinds != {Kind.CAPACITOR})
                    sel_label.classes(remove="text-negative")
                    if not targets:
                        sel_label.text = "Select rows to give them an exception"
                    elif len(kinds) > 1:
                        sel_label.text = "Selection mixes capacitors and resistors — select one type at a time"
                        sel_label.classes(add="text-negative")
                    else:
                        noun = "capacitor" if Kind.CAPACITOR in kinds else "resistor"
                        names = ", ".join(targets)
                        names = names if len(names) <= 90 else names[:89] + "…"
                        sel_label.text = f"{len(targets)} {noun}{'s' if len(targets) != 1 else ''} selected: {names}"

                async def on_selection() -> None:
                    show_selection(await selected_designators())

                show_selection([])
                review.on("selectionChanged", on_selection)

                def select_kind(kind: Kind) -> None:
                    review.run_grid_method("deselectAll")
                    for r in session.designator_rows():
                        if r["kind"] is kind:
                            review.run_row_method(r["designator"], "setSelected", True)

                async def apply_override() -> None:
                    targets = await selected_designators()
                    if not targets:
                        ui.notify("Select one or more rows first", type="warning")
                        return
                    changes: dict[str, object] = {}
                    try:
                        if ov_diel.enabled and ov_diel.value != KEEP:
                            changes["dielectric"] = None if ov_diel.value == ANY else ov_diel.value
                        for name, inp in (("min_voltage", ov_v), ("min_power", ov_w), ("tolerance", ov_t)):
                            if inp.enabled and (inp.value or "").strip():
                                changes[name] = coerce_field(name, inp.value)
                    except (ValueError, ZeroDivisionError) as e:
                        ui.notify(f"Invalid value: {e}", type="negative")
                        return
                    if not changes:
                        ui.notify("Nothing to change", type="warning")
                        return
                    try:
                        session.apply_exception(targets, changes)
                    except ValueError as e:
                        ui.notify(str(e), type="negative")
                        return
                    ov_diel.value = KEEP
                    for inp in (ov_v, ov_w, ov_t):
                        inp.value = ""
                    refresh_review()
                    ui.notify(f"Updated {len(targets)} part(s): {', '.join(targets)}")

                async def reset_override() -> None:
                    for d in await selected_designators():
                        s.clear_override(d)
                    refresh_review()

                async def start_pick() -> None:
                    problem = session.check_ready()
                    if problem:
                        ui.notify(problem, type="negative")
                        if "credentials" in problem:
                            keys_dialog.open()
                        return
                    stepper.next()
                    await do_pick()

                with ui.stepper_navigation():
                    ui.button("Back", on_click=stepper.previous).props("flat")
                    ui.button("Pick parts", icon="search", on_click=start_pick)

            # ---- step 4: results
            with ui.step("Results"):
                status = ui.label("")
                bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                results_grid = ui.aggrid(
                    {
                        "columnDefs": [
                            {"field": "designators", "headerName": "Designators", "width": 170, "pinned": "left", "tooltipField": "designators"},
                            {"field": "value", "headerName": "Value", "width": 85},
                            {"field": "size", "headerName": "Size", "width": 70},
                            {"field": "spec", "headerName": "Spec", "width": 120},
                            {"field": "mpn", "headerName": "MPN", "width": 190, "cellClassRules": {"bad": "!data.ok"}},
                            {"field": "manufacturer", "headerName": "Manufacturer", "width": 140},
                            {"field": "supplier_part", "headerName": "Supplier PN", "width": 170},
                            {"field": "needed", "headerName": "Need", "width": 75, "type": "rightAligned"},
                            {"field": "order", "headerName": "Order", "width": 80, "type": "rightAligned"},
                            {"field": "total", "headerName": "Total", "width": 85, "type": "rightAligned"},
                            {"field": "stock", "headerName": "Stock", "width": 100, "type": "rightAligned"},
                            {"field": "notes", "headerName": "Notes", "flex": 1, "minWidth": 200, "tooltipField": "notes"},
                        ],
                        "rowData": [],
                        "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
                        "tooltipShowDelay": 300,
                    },
                    theme="balham",
                ).classes("w-full h-[28rem]")
                results_grid.on("cellDoubleClicked", lambda e: show_alternatives(e.args["rowIndex"]))
                total_label = ui.label("").classes("text-lg font-bold")
                ui.label("Double-click a row for alternatives or nearest standard values.").classes("text-sm opacity-70")

                def download() -> None:
                    if session.results:
                        ui.download.content(session.output_bytes(), session.output_name(), "text/csv")

                def save_local() -> None:
                    try:
                        path = session.save()
                        ui.notify(f"Saved {path}", type="positive")
                    except OSError as e:
                        ui.notify(f"Could not save: {e}", type="negative")

                async def nearest() -> None:
                    if session.accept_all_nearest():
                        await do_pick()
                    else:
                        ui.notify("No non-standard values to substitute")

                with ui.stepper_navigation():
                    ui.button("Back", on_click=stepper.previous).props("flat")
                    ui.button("Use nearest std values", icon="tune", on_click=nearest).props("outline")
                    if not web:
                        ui.button("Save next to BOM", icon="save", on_click=save_local).bind_visibility_from(
                            session, "source_path", backward=lambda p: p is not None
                        )
                    ui.button("Download CSV", icon="download", on_click=download)
                    ui.button("Next: basket padding", icon="shopping_cart", on_click=stepper.next).bind_visibility_from(
                        s, "pad_enabled"
                    )

            # ---- step 5: basket padding
            with ui.step("Padding"):
                ui.markdown(
                    "Tick the lines you'd like more of and press **Pad selected**. The shortfall is split as "
                    "equal extra spend per line, using price breaks where they help. Lines without a price "
                    "can't be ticked."
                ).classes("text-sm")
                pad_label = ui.label("").classes("text-lg font-bold")
                pad_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                pad_status = ui.label("")
                pad_grid = ui.aggrid(
                    {
                        "columnDefs": [
                            {"field": "designators", "headerName": "Designators", "width": 170, "pinned": "left", "tooltipField": "designators"},
                            {"field": "part", "headerName": "Part", "width": 200, "cellClassRules": {"bad": "!data.priced"}},
                            {"field": "supplier_part", "headerName": "Supplier PN", "width": 170},
                            {"field": "needed", "headerName": "Need", "width": 75, "type": "rightAligned"},
                            {"field": "order", "headerName": "Order", "width": 80, "type": "rightAligned"},
                            {"field": "padded", "headerName": "+Pad", "width": 80, "type": "rightAligned", "cellClassRules": {"ov": "data.padded"}},
                            {"field": "unit", "headerName": "Unit", "width": 85, "type": "rightAligned"},
                            {"field": "total", "headerName": "Total", "width": 85, "type": "rightAligned"},
                            {"field": "notes", "headerName": "Notes", "flex": 1, "minWidth": 200, "tooltipField": "notes"},
                        ],
                        "rowData": [],
                        "rowSelection": {
                            "mode": "multiRow",
                            "checkboxes": True,
                            "headerCheckbox": False,
                            "enableClickSelection": True,
                            ":isRowSelectable": "(node) => node.data && node.data.priced",
                        },
                        "selectionColumnDef": {"pinned": "left", "width": 44},
                        ":getRowId": "(params) => String(params.data.index)",
                    },
                    theme="balham",
                ).classes("w-full h-[28rem]")

                async def pad_selected() -> None:
                    indices = [r["index"] for r in await pad_grid.get_selected_rows()]
                    if not indices:
                        ui.notify("Tick the lines to increase first", type="warning")
                        return
                    outcome = session.pad(indices)
                    cur = session.currency
                    if outcome.status == ALREADY_MET:
                        pad_status.text = "Threshold already met — nothing to pad"
                    elif outcome.status == UNREACHABLE:
                        pad_status.text = (
                            f"Stock limits stop these lines reaching the threshold — padded by {outcome.added:.2f} {cur}. Tick more lines."
                        )
                    else:
                        pad_status.text = f"Padded by {outcome.added:.2f} {cur} across {len(indices)} line(s)"
                    refresh_padding(keep=indices)
                    refresh_results()

                def clear_padding() -> None:
                    session.clear_padding()
                    pad_status.text = "Padding cleared"
                    refresh_padding()
                    refresh_results()

                with ui.stepper_navigation():
                    ui.button("Back", on_click=stepper.previous).props("flat")
                    ui.button("Pad selected", icon="add_shopping_cart", on_click=pad_selected)
                    ui.button("Clear padding", icon="undo", on_click=clear_padding).props("outline")
                    if not web:
                        ui.button("Save next to BOM", icon="save", on_click=save_local).bind_visibility_from(
                            session, "source_path", backward=lambda p: p is not None
                        )
                    ui.button("Download CSV", icon="download", on_click=download)

    alt_dialog = ui.dialog()

    # ---------------------------------------------------------------- helpers

    def refresh_review() -> None:
        rows = []
        for r in session.designator_rows():
            d, spec, kind = r["designator"], r["spec"], r["kind"]
            ov = s.overrides.get(d, {})
            is_cap = kind is Kind.CAPACITOR
            rows.append(
                {
                    "designator": d,
                    "value": format_value(r["value"], kind) if r["value"] is not None else f"? {r['value_text']}",
                    "footprint": r["footprint"],
                    "size": r["size"],
                    "dielectric": (spec.dielectric or "any") if is_cap else "—",
                    "min_voltage": (field_text("min_voltage", spec.min_voltage) or "any") if is_cap else "—",
                    "min_power": (field_text("min_power", spec.min_power) or "any") if not is_cap else "—",
                    "tolerance": field_text("tolerance", spec.tolerance) or "any",
                    "kind": kind.value,
                    **{f"_ov_{k}": k in ov for k in ("value", "dielectric", "min_voltage", "min_power", "tolerance")},
                }
            )
        review.options["rowData"] = rows
        review.update()
        show_selection([])  # new row data clears the grid's selection

    def refresh_results() -> None:
        rows = []
        for r in session.results:
            ln, p = r.line, r.pick
            rows.append(
                {
                    "designators": ",".join(ln.designators),
                    "value": format_value(ln.value, ln.kind) if ln.value is not None else ln.row.value_text,
                    "size": ln.size or "?",
                    "spec": ln.spec.describe(ln.kind),
                    "mpn": p.candidate.mpn if p else "NOT PICKED",
                    "manufacturer": p.candidate.manufacturer if p else "",
                    "supplier_part": p.candidate.supplier_part if p else "",
                    "needed": ln.needed,
                    "order": p.order_qty if p else None,
                    "total": f"{p.line_total:.2f}" if p else "",
                    "stock": p.candidate.stock if p else None,
                    "notes": "; ".join(x for x in (r.note, *qty_notes(p)) if x),
                    "ok": bool(p),
                }
            )
        results_grid.options["rowData"] = rows
        results_grid.update()
        missing = sum(1 for r in session.results if not r.ok)
        total_label.text = basket_text() + (f" — {missing} line(s) not picked" if missing else "")

    def qty_notes(p) -> list[str]:
        if not p:
            return []
        return [x for x in (f"+{p.spares} spares" if p.spares else "", f"+{p.padded} padding" if p.padded else "") if x]

    def basket_text() -> str:
        cur = session.currency
        if not s.pad_enabled:
            return f"Total: {session.total:.2f} {cur}"
        text = f"Basket: {session.total:.2f} / {s.pad_threshold:.2f} {cur}"
        return text + (f" ({session.shortfall:.2f} short)" if session.shortfall else " ✓")

    def refresh_padding(keep: list[int] = ()) -> None:
        rows = []
        for i, x in enumerate(session.basket()):
            p = x.pick
            rows.append(
                {
                    "index": i,
                    "designators": ",".join(x.designators),
                    "part": p.candidate.mpn if p else getattr(x, "query", "") or x.row.value_text,
                    "supplier_part": p.candidate.supplier_part if p else "",
                    "needed": x.needed,
                    "order": p.order_qty if p else None,
                    "padded": f"+{p.padded}" if p and p.padded else "",
                    "unit": f"{p.unit_price:.4g}" if p else "",
                    "total": f"{p.line_total:.2f}" if p else "",
                    "notes": x.note,
                    "priced": bool(p),
                }
            )
        pad_grid.options["rowData"] = rows
        pad_grid.update()
        for i in keep:  # new row data clears the selection
            pad_grid.run_row_method(str(i), "setSelected", True)
        unpriced = session.unpriced_count
        pad_label.text = basket_text() + (f" — {unpriced} line(s) unpriced, counted as 0" if unpriced else "")
        pad_bar.value = min(1.0, session.total / s.pad_threshold) if s.pad_threshold else 0

    def on_progress(done: int, total: int, label: str) -> None:
        progress.update(done=done, total=total, label=label)

    def tick() -> None:
        if progress["running"]:
            t = max(progress["total"], 1)
            bar.value = progress["done"] / t
            status.text = f"Searching DigiKey… {progress['done']}/{progress['total']} {progress['label']}"

    ui.timer(0.2, tick)

    async def do_pick() -> None:
        if progress["running"]:
            return
        if web and limiter and not limiter.allow(client_ip):
            ui.notify("Rate limit reached — please try again later", type="negative")
            return
        progress.update(running=True, done=0, total=0, label="")
        bar.visible = True
        try:
            await run.io_bound(session.run, on_progress)
        except SupplierError as e:
            status.text = str(e)
            ui.notify(str(e), type="negative", timeout=15000)
            if isinstance(e, SupplierAuthError):
                keys_dialog.open()
            return
        finally:
            progress["running"] = False
            bar.visible = False
        status.text = f"Done — {len(session.results)} lines searched"
        refresh_results()
        if s.pad_enabled:
            pad_status.text = ""
            refresh_padding()

    def show_alternatives(index: int) -> None:
        r: LineResult = session.results[index]
        alt_dialog.clear()
        with alt_dialog, ui.card().classes("w-[48rem] max-w-full"):
            ui.label(f"{', '.join(r.line.designators)} — {r.line.spec.describe(r.line.kind)}").classes("font-bold")
            if r.note:
                ui.label(r.note).classes("text-sm opacity-80")
            for i, p in enumerate(r.picks):
                c = p.candidate

                def choose(i=i) -> None:
                    session.select(index, i)
                    refresh_results()
                    if s.pad_enabled:
                        refresh_padding()
                    alt_dialog.close()

                with ui.row().classes("w-full items-center justify-between border-b py-1"):
                    with ui.column().classes("gap-0"):
                        with ui.row().classes("items-center gap-2"):
                            if i == r.selected:
                                ui.icon("check_circle", color="positive")
                            if c.url:
                                ui.link(c.mpn, c.url, new_tab=True).classes("font-mono")
                            else:
                                ui.label(c.mpn).classes("font-mono")
                            ui.label(f"{c.manufacturer} · {c.supplier_part} · {c.packaging}").classes("text-sm opacity-70")
                        ui.label(
                            f"order {p.order_qty} @ {p.unit_price:.4g} = {p.line_total:.2f} · stock {c.stock:,}"
                        ).classes("text-sm")
                    ui.button("Use", on_click=choose).props("dense")
            for v in r.suggestions:

                async def use_value(v=v) -> None:
                    session.accept_suggestion(index, v)
                    alt_dialog.close()
                    await do_pick()

                ui.button(f"Use {format_value(v, r.line.kind)} instead", icon="refresh", on_click=use_value).props("outline")
            if not r.picks and not r.suggestions:
                ui.label("No alternatives — go back and relax the spec for these designators.")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=alt_dialog.close).props("flat")
        alt_dialog.open()


def _register(web: bool) -> None:
    limiter = RateLimiter(_env_int("CBAPICK_RATE_PER_HOUR", 20)) if web else None
    max_rows = _env_int("CBAPICK_MAX_ROWS", 500)

    @ui.page("/")
    def index() -> None:
        build_page(web, limiter, max_rows)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}


def serve(a: argparse.Namespace | None = None) -> None:
    """Host as a website. Configure with CBAPICK_* environment variables."""
    _register(web=True)
    host = (a and a.host) or os.environ.get("CBAPICK_HOST", "0.0.0.0")
    port = (a and a.port) or _env_int("CBAPICK_PORT", 8080)
    ui.run(
        host=host,
        port=port,
        title="cbapick4me",
        reload=False,
        show=False,
        storage_secret=os.environ.get("CBAPICK_STORAGE_SECRET") or None,
        show_welcome_message=True,
        favicon="🔎",
    )


def desktop(a: argparse.Namespace | None = None) -> None:
    """Native window (pywebview). Falls back to opening the local browser."""
    _register(web=False)
    use_native = importlib.util.find_spec("webview") is not None
    if use_native:
        app.native.settings["ALLOW_DOWNLOADS"] = True
    ui.run(
        host="127.0.0.1",
        port=(a and a.port) or native.find_open_port(),
        title="cbapick4me",
        reload=False,
        native=use_native,
        show=not use_native,
        window_size=(1280, 860) if use_native else None,
        favicon="🔎",
    )
