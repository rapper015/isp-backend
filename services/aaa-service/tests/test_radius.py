from app.radius import AttributeValidationError, normalize_attributes, normalize_mac, safe_reply, traffic_counter

def test_normalization_and_redaction():
    attributes, diagnostic = normalize_attributes({"User-Name": " Alice ", "Calling-Station-Id": "AA-BB-CC-DD-EE-FF", "User-Password": "never-log", "Unknown-VSA": "debug"})
    assert attributes["User-Name"] == "alice"
    assert attributes["Calling-Station-Id"] == "aa:bb:cc:dd:ee:ff"
    assert diagnostic["Unknown-VSA"] == "debug"

def test_invalid_mac_is_rejected():
    try: normalize_mac("not-a-mac")
    except AttributeValidationError: return
    assert False

def test_64_bit_counter_and_safe_reply():
    assert traffic_counter({"Acct-Input-Gigawords": 1, "Acct-Input-Octets": 3}, "Input") == 4294967299
    assert safe_reply({"Mikrotik-Rate-Limit": "10M/20M", "Unknown": "no"}) == {"Mikrotik-Rate-Limit": "10M/20M"}
