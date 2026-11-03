from tuya_lds_vacuum_map.lds.codec import decode_name, decode_raw, encode_name, encode_raw


def test_names_are_base36_char_codes():
    assert decode_name("2b+2p+30+2p") == "Sala"
    assert decode_name("2b+39+6l+38+2t") == "Suíte"
    assert encode_name("Suíte") == "2b+39+6l+38+2t"
    assert decode_name(encode_name("Office 2")) == "Office 2"


def test_empty_names():
    assert decode_name("") == ""
    assert decode_name(None) == ""


def test_raw_values_are_base64_json():
    payload = {"infoType": 21004, "data": {"name": "Suíte"}}
    assert decode_raw(encode_raw(payload)) == payload
