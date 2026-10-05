# cbapick4me

Picks specific capacitors and resistors for an **EasyEDA BOM** from **DigiKey**. BOMs from other EDA tools work too: KiCad, Altium, Eagle, or any CSV.

Your schematic says "10u, C1206" and "4.7k, R0603". cbapick4me turns those into real DigiKey part numbers: the cheapest parts in stock that match the value, chip size, dielectric, voltage, power and tolerance you ask for. It writes `<bom>_picked4u.csv`, ready to upload to DigiKey's BOM Manager.

[![cbapick4me demo video](https://img.youtube.com/vi/JGXSiaPnGtc/maxresdefault.jpg)](https://www.youtube.com/watch?v=JGXSiaPnGtc)

1. **Load** the BOM CSV.
   - EasyEDA Standard and Pro exports are recognised automatically. This is the default.
   - For any other EDA tool, switch on **Custom CSV** and choose which column holds the designator, value, footprint and so on. Your choices are remembered, and you can save them as named presets. See [Other EDA tools](docs/usage.md#other-eda-tools-custom-csv).
2. **Set blanket specs**:
   - capacitors: dielectric (X7R/X5R/C0G…), minimum voltage, tolerance
   - resistors: minimum power, tolerance
   - the number of boards
3. **Add exceptions** per designator, for example C9 → X5R.
4. **Pick.**
   - It searches DigiKey's official API, matching value, chip size and ratings exactly.
   - It picks the cheapest part with plenty of stock.
   - It adds a few cheap spares when a price break makes them nearly free.
5. **Save** the CSV.
   - ICs, connectors, LEDs and other lines pass through unchanged.
   - Quantities are multiplied by the board count.
   - Optional: pad the basket up to DigiKey's free-shipping threshold.

## Quick start

| | Windows | Linux / macOS |
|---|---|---|
| **Get it** | Download `cbapick4me-…-windows-x64.exe` from the [**Releases page**](https://github.com/Sennadachi/cbapick4me/releases/latest) | `pipx install "git+https://github.com/Sennadachi/cbapick4me"` |
| **Run it** | Double-click the exe. A window opens. | `cbapick4me path/to/BOM.csv`. A terminal UI opens. |
| **Full guide** | [docs/windows.md](docs/windows.md) | [docs/linux.md](docs/linux.md) |

Then:

1. **Get your own free DigiKey API key.** This takes about 5 minutes; follow [docs/api-key.md](docs/api-key.md).
2. **Enter it in the app.** In the GUI, click **API keys**. In the terminal UI, press **Ctrl+K**. Then click **Test DigiKey** to check it.
3. **Load your BOM and follow the steps.** See [docs/usage.md](docs/usage.md).

> [!WARNING]
> **Keep your DigiKey API key private.**
> - The Client Secret works like a password for your DigiKey developer account and its daily quota.
> - Don't share it, post it in GitHub issues, show it in screenshots or screen shares, or commit it to git. That includes `.env` and `config.toml`.
> - Everyone who uses cbapick4me should create their own key. They're free.
> - If your key leaks, regenerate the secret on DigiKey's developer site straight away. [More on this](docs/api-key.md#keep-your-key-private).

## Documentation

- [Windows install](docs/windows.md)
- [Linux / macOS install](docs/linux.md) (pipx, plus the optional `cba2pick` fuzzy-finder launcher)
- [Getting a DigiKey API key](docs/api-key.md), and keeping it safe
- [How to use it](docs/usage.md): GUI, terminal UI, command line, [other EDA tools (Custom CSV)](docs/usage.md#other-eda-tools-custom-csv), how parts are chosen, basket padding, output columns
- [Hosting it as a website](docs/hosting.md) (Docker)
- [Development and releases](docs/development.md)

## License

[MIT](LICENSE). This project isn't affiliated with DigiKey, EasyEDA or any other EDA vendor. Check the parts it picks before you order.
