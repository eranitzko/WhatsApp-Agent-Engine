from app.db.models import UserAccount, CrossGroupConfirmation, SplitTransaction, GroupRegistry

def test_user_account_has_expected_columns():
    cols = {c.key for c in UserAccount.__table__.columns}
    assert {"id", "phone", "group_jid", "role", "created_at"} <= cols

def test_cross_group_confirmation_has_expected_columns():
    cols = {c.key for c in CrossGroupConfirmation.__table__.columns}
    assert {
        "id", "split_transaction_id", "initiator_phone", "initiator_group_jid",
        "target_phone", "target_group_jid", "action_type", "action_payload",
        "status", "expires_at", "created_at",
    } <= cols

def test_split_transaction_has_expected_columns():
    cols = {c.key for c in SplitTransaction.__table__.columns}
    assert {
        "id", "reporter_group_jid", "reporter_phone", "payer_phone",
        "total_amount", "description", "status", "created_at",
    } <= cols

def test_group_registry_has_group_type():
    cols = {c.key for c in GroupRegistry.__table__.columns}
    assert "group_type" in cols
