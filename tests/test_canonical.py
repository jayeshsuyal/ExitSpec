import json

import pytest

from exitspec.canonical import CanonicalizationError, canonical_json_bytes


def test_rfc_8785_section_3_2_3_serialization_vector():
    sample = json.loads(
        r'''{
          "numbers": [333333333.33333329, 1E30, 4.50, 2e-3,
                      0.000000000000000000000000001],
          "string": "\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/",
          "literals": [null, true, false]
        }'''
    )
    expected = bytes.fromhex(
        "7b 22 6c 69 74 65 72 61 6c 73 22 3a 5b 6e 75 6c 6c 2c 74 72 "
        "75 65 2c 66 61 6c 73 65 5d 2c 22 6e 75 6d 62 65 72 73 22 3a "
        "5b 33 33 33 33 33 33 33 33 33 2e 33 33 33 33 33 33 33 2c 31 "
        "65 2b 33 30 2c 34 2e 35 2c 30 2e 30 30 32 2c 31 65 2d 32 37 "
        "5d 2c 22 73 74 72 69 6e 67 22 3a 22 e2 82 ac 24 5c 75 30 30 "
        "30 66 5c 6e 41 27 42 5c 22 5c 5c 5c 5c 5c 22 2f 22 7d"
    )

    assert canonical_json_bytes(sample) == expected


def test_rfc_8785_section_3_2_3_utf16_property_order_vector():
    sample = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }

    canonical = json.loads(canonical_json_bytes(sample))

    assert list(canonical.values()) == [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]


def test_nested_objects_are_sorted_recursively_without_reordering_arrays():
    first = {
        "z": [{"b": 2, "a": 1}, {"\u00e9": "precomposed", "e\u0301": "decomposed"}],
        "a": {"d": 4, "c": 3},
    }
    same_value_different_order = {
        "a": {"c": 3, "d": 4},
        "z": [{"a": 1, "b": 2}, {"e\u0301": "decomposed", "\u00e9": "precomposed"}],
    }

    expected = (
        '{"a":{"c":3,"d":4},"z":[{"a":1,"b":2},'
        '{"e\u0301":"decomposed","\u00e9":"precomposed"}]}'
    ).encode("utf-8")

    assert canonical_json_bytes(first) == expected
    assert canonical_json_bytes(same_value_different_order) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_fail_clearly(value):
    with pytest.raises(CanonicalizationError, match="RFC 8785 canonicalization failed"):
        canonical_json_bytes({"value": value})


@pytest.mark.parametrize(
    "value",
    [
        {"value": 2**60},
        {"value": object()},
        {1: "non-string key"},
        {"value": "\ud800"},
    ],
)
def test_values_outside_supported_json_domain_fail_clearly(value):
    with pytest.raises(CanonicalizationError, match="RFC 8785 canonicalization failed"):
        canonical_json_bytes(value)
