import pytest

from cbapick4me.core.parse import (
    Kind,
    classify,
    format_value,
    keyword_values,
    is_standard_resistance,
    nearest_standard,
    parse_footprint,
    parse_value,
)

C, R = Kind.CAPACITOR, Kind.RESISTOR


@pytest.mark.parametrize(
    "text,kind,expected",
    [
        ("100nF", C, 100e-9),
        ("0.1uF", C, 0.1e-6),
        ("0.1µF", C, 0.1e-6),
        ("0.1u", C, 0.1e-6),
        ("10u", C, 10e-6),
        ("0.01u", C, 10e-9),
        ("100n", C, 100e-9),
        ("1n5", C, 1.5e-9),
        ("22pF", C, 22e-12),
        ("4.7k", R, 4700),
        ("4k7", R, 4700),
        ("10K", R, 10_000),
        ("10", R, 10),
        ("0R", R, 0),
        ("0Ω", R, 0),
        ("1M", R, 1e6),
        ("2R2", R, 2.2),
        ("5.1k", R, 5100),
        ("49.9Ω", R, 49.9),
        ("10kohm", R, 10_000),
    ],
)
def test_parse_value(text, kind, expected):
    assert parse_value(text, kind) == pytest.approx(expected)


@pytest.mark.parametrize("text,kind", [("abc", C), ("", R), ("10R", C)])
def test_parse_value_rejects(text, kind):
    assert parse_value(text, kind) is None


@pytest.mark.parametrize(
    "fp,size",
    [
        ("C0603", "0603"),
        ("R0805", "0805"),
        ("C1206", "1206"),
        ("R_0805_2012Metric", "0805"),
        ("C_0402_1005Metric", "0402"),
        ("0402_C", "0402"),
        ("CAP_1608", "0603"),
        ("SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR", None),
        ("LED0805-RD_RED", "0805"),
    ],
)
def test_parse_footprint(fp, size):
    assert parse_footprint(fp) == size


@pytest.mark.parametrize(
    "des,kind",
    [("C1", C), ("c22", C), ("R25", R), ("CN1", Kind.OTHER), ("LED1", Kind.OTHER), ("RN1", Kind.OTHER), ("PROG", Kind.OTHER), ("ZD1", Kind.OTHER)],
)
def test_classify(des, kind):
    assert classify(des) is kind


def test_format_value():
    assert format_value(100e-9, C) == "100nF"
    assert format_value(10e-6, C) == "10µF"
    assert format_value(22e-12, C) == "22pF"
    assert format_value(4700, R) == "4.7kΩ"
    assert format_value(49.9, R) == "49.9Ω"
    assert format_value(1e6, R) == "1MΩ"


def test_nearest_standard_for_50_ohm():
    assert not is_standard_resistance(50)
    assert nearest_standard(50) == [pytest.approx(49.9), pytest.approx(51)]
    assert is_standard_resistance(49.9)
    assert is_standard_resistance(10_000)
    assert is_standard_resistance(5100)


def test_keyword_values_cover_supplier_spellings():
    assert keyword_values(100e-9, C) == ["0.1uF", "100000pF", "100nF"]
    assert keyword_values(10e-9, C) == ["0.01uF", "10000pF", "10nF"]
    assert keyword_values(10e-6, C) == ["10uF"]
    assert keyword_values(22e-12, C) == ["22pF"]
    assert keyword_values(4700, R) == ["4.7k"]
