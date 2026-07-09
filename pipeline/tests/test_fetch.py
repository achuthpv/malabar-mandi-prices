from mandi.fetch import _redact


def test_api_key_redacted_from_error_text():
    msg = ("404 Client Error for url: https://api.data.gov.in/resource/xyz"
           "?api-key=579b464db66ec23bdd000001abcdef&format=json&offset=0")
    out = _redact(msg)
    assert "579b464d" not in out
    assert "api-key=REDACTED" in out
    assert "format=json" in out  # rest of the message survives
