from app.db.models import GroupRegistry


def test_group_registry_has_custom_instructions_column(db):
    entry = GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="invoice_curator",
        custom_instructions="Work invoices only. USD.",
    )
    db.add(entry)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupRegistry, "123@g.us")
    assert fetched.custom_instructions == "Work invoices only. USD."


def test_custom_instructions_defaults_to_none(db):
    entry = GroupRegistry(group_jid="456@g.us", blueprint_id="invoice_curator")
    db.add(entry)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupRegistry, "456@g.us")
    assert fetched.custom_instructions is None
