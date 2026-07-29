from app.utils.phone import resolve_sender_phone


def test_resolve_sender_phone_prefers_resolved_phone():
    ctx = {"resolved_phone": "972523206175", "sender": "175715853041683@lid"}
    assert resolve_sender_phone(ctx) == "972523206175"


def test_resolve_sender_phone_falls_back_to_raw_sender():
    ctx = {"sender": "972523206175@s.whatsapp.net"}
    assert resolve_sender_phone(ctx) == "972523206175"


def test_resolve_sender_phone_falls_back_to_raw_lid_when_unresolved():
    ctx = {"sender": "6541369471061@lid"}
    assert resolve_sender_phone(ctx) == "6541369471061"


def test_resolve_sender_phone_empty_ctx_returns_empty_string():
    assert resolve_sender_phone({}) == ""
