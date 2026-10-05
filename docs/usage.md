# Using cbapick4me

- [Export the BOM from EasyEDA](#1-export-the-bom-from-easyeda)
- [Other EDA tools (Custom CSV)](#other-eda-tools-custom-csv)
- [GUI (Windows)](#gui-windows)
- [Terminal UI (Linux)](#terminal-ui-linux)
- [Command line / headless](#command-line--headless)
- [How parts are chosen](#how-parts-are-chosen)
- [Basket padding](#basket-padding)
- [Output file](#output-file) and [ordering from DigiKey](#ordering-from-digikey)

You need a [DigiKey API key](api-key.md) entered first.

## 1. Export the BOM from EasyEDA

- **EasyEDA Standard:** in the schematic or PCB editor, choose **Fabrication → BOM** (or **Export → BOM**), then **Export BOM**. You get a UTF-16, tab-separated `.csv`.
- **EasyEDA Pro:** **Export → Bill of Materials (BOM)** → CSV. You get a UTF-8, comma-separated `.csv`.

Either format works as exported; you don't need to change anything. Capacitors are recognised by `C` designators and resistors by `R` designators. The tool reads each part's value from the **Name/Value** column and its chip size from the **Footprint** column, for example `C0603` or `R0805`.

Using KiCad, Altium, Eagle or another tool? See [Other EDA tools](#other-eda-tools-custom-csv).

## Other EDA tools (Custom CSV)

EasyEDA is the default. For any other tool's BOM, switch on **Custom CSV** and tell cbapick4me which column holds what.

**What your BOM needs**
- One row per part or group of parts. Designators can be grouped in one cell, for example `C1,C2,C5` or `R1 R2`.
- Capacitors must have designators starting with **C**, and resistors with **R**, for example `C12` or `R3`. Every other line, such as ICs and connectors, is kept in the output unchanged.
- The **footprint** must contain the chip size. `0603`, `C0603`, `R_0805_2012Metric` and `Capacitor_SMD:C_0603_1608Metric` all work.
- Values such as `100n`, `0.1uF`, `4k7`, `4.7k` and `10R` are all understood.

**Columns you can map**

| Item | Required | Example headers |
|---|---|---|
| Designator | **yes** | Reference, Designator, RefDes |
| Value | **yes** | Value, Comment |
| Footprint / package | needed for C/R | Footprint, Package |
| Quantity | no. If left out, it's the number of designators | Qty, Quantity |
| Manufacturer part, Manufacturer, Supplier part, Supplier, Price, Line no. | no | MPN, Mfr, DigiKey PN… |

The optional columns are filled in the output for picked parts. With basket padding on, other lines are priced by their manufacturer part.

**Other settings**
- **Delimiter:** comma, tab, semicolon, or auto-detect.
- **Header row:** the line number that holds the column names. Some tools write a title or date above the table. In KiCad's default export the header is on line 1. If your file has 2 lines of preamble, the header row is 3.

**Typical mappings**

| Tool | Designator | Value | Footprint | Quantity |
|---|---|---|---|---|
| KiCad (BOM export / Symbol Fields Table) | Reference | Value | Footprint | Qty |
| Altium | Designator | Comment | Footprint | Quantity |
| Eagle / Fusion Electronics | Parts | Value | Package | Qty |

Your exact headers may differ. The mapping form shows a preview of the first rows, so you can check before you continue.

### In the GUI
1. On the **BOM** step, switch the toggle from **EasyEDA** to **Custom CSV**.
2. Drop or open your CSV. A **Columns** card appears with:
   - the column choices, pre-filled by guessing from the header names
   - **Delimiter** and **Header row** settings
   - a live preview
3. Fix anything that's wrong, then click **Use these columns**.
4. **Presets:** type a name and click **Save preset**. Next time, pick it from **Presets** and click **Load**. **Delete** removes a preset.

### In the terminal UI
1. On the Open screen, press **Ctrl+T**, or click the **Custom CSV columns** switch.
2. Open your file. A **Custom CSV columns** dialog asks for:
   - the delimiter
   - the header row
   - a column for each item
   - presets: **Load**, **Save preset**, **Delete**

   The preview underneath updates as you choose.
3. Choose **Use columns**.

### On the command line

```bash
# map the columns directly
cbapick4me --headless board.csv --header-row 3 \
           --col designator=Reference --col value=Value --col footprint=Footprint --col quantity=Qty
# use a preset you saved in the GUI or TUI
cbapick4me --headless board.csv --preset KiCad
# --col / --preset also work with the TUI: it skips the dialog
cbapick4me board.csv --preset KiCad
# force EasyEDA mode for one run, even if Custom CSV is switched on
cbapick4me --format easyeda BOM.csv
```

If Custom CSV is switched on and you give no `--col` or `--preset`, headless mode uses the mapping you used last.

### What's remembered
- Whether Custom CSV is on.
- The last mapping you used. It's pre-filled the next time, as long as the file has the same headers; otherwise the columns are guessed by name.
- Your named presets.

They're stored in `bom_formats.toml` next to your API keys' `config.toml`, but in a separate file, so it's safe to share:
- Windows: `%APPDATA%\cbapick4me\bom_formats.toml`
- Linux: `~/.config/cbapick4me/bom_formats.toml`

On the [hosted website](hosting.md), presets and mappings only last until the browser tab is closed.

## GUI (Windows)

Double-click the exe on Windows. On Linux or macOS, run `cbapick4me --gui`. The window walks you through these steps:

1. **BOM.** Leave the toggle on **EasyEDA**, or switch it to **Custom CSV** for [other tools](#other-eda-tools-custom-csv). Drop the CSV onto the box, or click **Open file…**. It shows how many capacitors and resistors it found; everything else is kept as-is.
2. **Specs.** Set the blanket rules that apply to every part unless you add an exception:
   - Number of boards. All quantities are multiplied by this.
   - Capacitors: dielectric (X7R, X5R, C0G…), minimum rated voltage, maximum tolerance.
   - Resistors: minimum power (`0.1`, `1/10W` and `100mW` all work), maximum tolerance.
   - Pricing: when to add cheap spares, and optionally **basket padding** (see [below](#basket-padding)).
3. **Exceptions.** Override the rules for particular designators, for example a C0G timing capacitor or a 1 W resistor:
   - Tick parts, or Ctrl/Shift-click them, or use **Select all capacitors / resistors**.
   - Set the fields you want, then click **Apply**. Fields that don't apply to the selected type are greyed out.
   - Grouped BOM rows are split automatically when only some of their designators get an exception.
   - **Reset to blanket** removes the exception.
4. **Pick parts.** It searches DigiKey, which takes a few seconds per line the first time, and shows the chosen part, price and stock for each line.
   - **Double-click a row** to see alternatives, and pick a different part.
   - If a resistor value isn't a standard value (for example `50` Ω), it isn't picked. Click **Use nearest std values** to take 49.9 Ω or 51 Ω instead.
5. **Save.** **Save next to BOM** writes `<bom>_picked4u.csv` beside the original. **Download CSV** lets you choose where to save it.
6. Optional: **Next: basket padding.** See [below](#basket-padding).

Click **API keys** at the top at any time to change your keys or DigiKey site.

## Terminal UI (Linux)

```bash
cbapick4me path/to/BOM.csv     # open a BOM directly
cbapick4me                     # browse for a BOM in the current folder
cba2pick                       # fuzzy-find a BOM (see linux.md)
```

The bottom bar always shows the keys for the current screen.

| Screen | Key | Action |
|---|---|---|
| Any | `Ctrl+K` | API keys |
| **Open** | type a path + `Enter`, or select in the tree | Open the BOM |
| | `Ctrl+T` | Custom CSV columns on/off ([other EDA tools](#other-eda-tools-custom-csv)) |
| **Setup** (blanket specs) | `Tab` / `Shift+Tab` | Move between fields |
| | `Ctrl+N` | Next: review |
| | `Ctrl+O` | Open a different BOM |
| **Review** (exceptions) | `Space` | Mark the row under the cursor |
| | `Shift+↑` / `Shift+↓` | Mark while moving |
| | `a` | Mark every part of the cursor row's type (all caps or all resistors) |
| | `e` | Edit an exception for the marked rows (or the cursor row) |
| | `r` | Reset the marked rows to the blanket specs |
| | `p` | Pick parts → |
| | `Esc` | Back |
| **Results** | `Enter` | Alternatives for this line (choose a different part) |
| | `n` | Use the nearest standard values for non-standard resistors |
| | `s` | Save the CSV next to the BOM |
| | `Ctrl+N` | Basket padding → |
| | `Esc` / `q` | Back / quit |
| **Padding** | `Space`, `Shift+↑/↓`, `a` | Mark lines to pad |
| | `p` | Pad the marked lines |
| | `c` | Clear padding |
| | `s` | Save the CSV |
| | `Esc` | Back to results |

**Theme:** on Omarchy, the TUI and GUI follow your current theme live. Set `CBAPICK_THEME=off` for the built-in look, or `CBAPICK_THEME=/path/to/colors.toml` to use a specific palette.

## Command line / headless

Use this for scripts, or when you already know what you want. It works the same on every OS:

```bash
cbapick4me --headless BOM.csv --boards 5 --dielectric X7R --cap-voltage 25 \
           --res-power 1/10W --override C9:dielectric=X5R --allow-nearest
cbapick4me --headless BOM.csv --dry-run          # show the searches without using the network
cbapick4me --headless BOM.csv -o order.csv       # choose the output path
cbapick4me --headless BOM.csv --pad-to 33 --pad R18,C5 --pad U6   # basket padding
cbapick4me --help                                # every option
```

| Option | Meaning (default) |
|---|---|
| `--boards N` | number of boards (1) |
| `--dielectric` | X7R, X5R, C0G… (X7R) |
| `--cap-voltage` | minimum capacitor rating in volts (16) |
| `--cap-tolerance` | max capacitor tolerance %, or `any` (any) |
| `--res-power` | minimum resistor power: `0.1`, `1/10W`, `100mW` (0.1) |
| `--res-tolerance` | max resistor tolerance %, or `any` (1) |
| `--override DES:field=val` | per-designator exception, e.g. `C9:dielectric=X5R` or `R4,R5:value=49.9`; repeatable |
| `--allow-nearest` | substitute the nearest E96/E24 value for non-standard resistors |
| `--cheap-threshold` / `--spare-budget` / `--stock-factor` | see [How parts are chosen](#how-parts-are-chosen) |
| `--pad-to AMOUNT` / `--pad DES,DES` | see [Basket padding](#basket-padding) |
| `--format easyeda\|custom`, `--col ROLE=HEADER`, `--preset NAME`, `--delimiter`, `--header-row N` | BOMs from other EDA tools; see [Custom CSV](#other-eda-tools-custom-csv) |

## How parts are chosen

| Setting | Default | Meaning |
|---|---|---|
| Stock factor | 10 | Only parts with stock ≥ needed × 10. If none qualify, the rule relaxes to ≥ needed, with a note. |
| Ranking | | Lowest cost to cover the requirement, then highest stock |
| Cheap threshold | 0.10 | Below this unit price, step up to higher price breaks for spares… |
| Spare budget | 2.00 | …as long as the extra spend per line stays within this |

- Value, chip size, dielectric, voltage, power and tolerance must match exactly. Ratings must be at least what you ask for.
- Packaging:
  - Cut Tape is preferred.
  - Tape & Reel is used only when its minimum order quantity (MOQ) makes sense.
  - Digi-Reel is never picked, because it carries a reeling fee.
- Non-standard resistor values such as `50` Ω get **no exact match**. The tool offers the nearest E96/E24 value (49.9 Ω / 51 Ω) but never substitutes it silently. Use `n` in the TUI, **Use nearest std values** in the GUI, or `--allow-nearest` in headless mode.
- Search results are cached for 24 h, so re-running after changing exceptions uses almost no API quota.

## Basket padding

DigiKey charges for shipping below a minimum order value. If your basket falls just short, padding spends the gap on extra parts instead of on shipping.

1. Turn on **basket padding** and enter the threshold at the start. That's the Setup screen in the TUI, the Specs step in the GUI, or `--pad-to 33` in headless mode. Use DigiKey's own currency, before VAT.
2. After the capacitors and resistors are picked, every other BOM line (ICs, LEDs, connectors…) is looked up on DigiKey by its `Manufacturer Part`. If that column is blank, its value is used instead. This costs one API call per line, and the results are cached for 24 h.
3. The whole BOM is then shown with the basket total and the shortfall. Mark the lines you'd like more of and pad them:
   - TUI: `Ctrl+N` on the results, `Space` to mark lines, then `p`
   - GUI: **Next: basket padding**, tick the lines, then **Pad selected**
   - headless: `--pad R18,C5 --pad U6`
4. The shortfall is split as **equal extra spend per marked line**.
   - Price breaks are used where they buy more parts for the same money.
   - A line that runs out of stock stops growing, and the other marked lines take up the rest.
   - If stock limits stop the marked lines reaching the threshold, you're told to mark more lines.

Lines that couldn't be found by MPN count as 0 and can't be padded.

## Output file

`<bom>_picked4u.csv` keeps all your original columns and lines.

- For picked parts it fills in `Manufacturer Part`, `Manufacturer`, `Supplier`, `Supplier Part`, `Price` and `Quantity`. `Quantity` is the **order** quantity.
- Added columns: `Qty Needed`, `Order Qty`, `Unit Price`, `Line Total`, `Stock`, `Pick Notes`.
- Lines that couldn't be picked say `NOT PICKED` in `Pick Notes`, with the reason.
- With basket padding:
  - Other lines found by MPN get DigiKey's part details and `Priced by MPN`.
  - Padded lines say `+N padding`, separately from `+N spares`.
- The file is UTF-8 with a byte-order mark (BOM), so Excel shows non-Latin manufacturer names, such as CJK ones, correctly.

## Ordering from DigiKey

1. Open <https://www.digikey.com/BOM> (BOM Manager) on your DigiKey site and upload `<bom>_picked4u.csv`.
2. Map **Supplier Part** to *DigiKey Part Number* and **Quantity** to *Quantity*.
3. Check any `NOT PICKED` lines yourself, then add the BOM to your cart.

Prices are DigiKey's standard list prices when the search ran. Always check the cart before you pay.
