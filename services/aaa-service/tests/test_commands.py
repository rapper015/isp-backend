from app.commands import DisabledRadiusCommandAdapter

def test_default_command_adapter_never_claims_delivery():
    result = DisabledRadiusCommandAdapter().send_disconnect("192.0.2.1", 3799, "not-a-real-secret", {"Acct-Session-Id": "x"})
    assert result.status == "FAILED"
