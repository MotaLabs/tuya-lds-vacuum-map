import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from support.strategies import json_values
from tuya_lds_vacuum_map.lds.codec import decode_name, decode_raw, encode_name, encode_raw


def test_names_are_base36_char_codes():
    assert decode_name("2b+2p+30+2p") == "Sala"
    assert decode_name("2b+39+6l+38+2t") == "Suíte"
    assert encode_name("Suíte") == "2b+39+6l+38+2t"
    assert decode_name(encode_name("Office 2")) == "Office 2"


def test_empty_names():
    assert decode_name("") == ""
    assert decode_name(None) == ""


@pytest.mark.parametrize("code", ["+", "1+", "-1", "1000000", 5, ["2b"]])
def test_malformed_names_are_value_errors(code):
    with pytest.raises(ValueError, match=r"name code|invalid literal|arg not in range"):
        decode_name(code)


def test_raw_values_are_base64_json():
    payload = {"infoType": 21004, "data": {"name": "Suíte"}}
    assert decode_raw(encode_raw(payload)) == payload


@given(st.text())
def test_any_text_survives_a_name_round_trip(text):
    assert decode_name(encode_name(text)) == text


@given(st.text())
def test_a_bad_name_code_is_a_value_error(code):
    with contextlib.suppress(ValueError):
        assert isinstance(decode_name(code), str)


@given(
    st.dictionaries(
        st.text(),
        st.recursive(
            st.none() | st.booleans() | st.integers() | st.text(), lambda c: st.lists(c) | st.dictionaries(st.text(), c)
        ),
    )
)
def test_any_object_survives_a_raw_round_trip(payload):
    assert decode_raw(encode_raw(payload)) == payload


@given(json_values.filter(lambda value: not isinstance(value, dict)))
def test_a_raw_value_must_be_an_object(value):
    with pytest.raises(ValueError, match="JSON object"):
        decode_raw(encode_raw(value))
