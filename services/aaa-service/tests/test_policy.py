from app.policy import calculate_policy

def test_policy_precedence_and_radius_rendering():
    policy = calculate_policy({"platform": {"upload_kbps": 1000, "download_kbps": 2000, "filter_id": "base"}, "tenant": {"download_kbps": 3000}, "quota": {"upload_kbps": 128, "download_kbps": 256}})
    assert policy.values["download_kbps"] == 256
    assert policy.provenance["download_kbps"] == "quota"
    assert policy.reply_attributes() == {"Mikrotik-Rate-Limit": "256k/128k", "Filter-Id": "base"}
