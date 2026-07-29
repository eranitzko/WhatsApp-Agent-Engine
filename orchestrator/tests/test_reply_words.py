from app.agent.reply_words import is_affirmative, is_negative


def test_is_affirmative_recognizes_union_of_all_known_confirm_words():
    for word in ["yes", "y", "ok", "confirm", "approve", "כן", "אישור", "יאללה", "YES", " yes "]:
        assert is_affirmative(word), f"{word!r} should be affirmative"


def test_is_negative_recognizes_union_of_all_known_cancel_words():
    for word in ["no", "n", "cancel", "abort", "reject", "לא", "ביטול", "NO", " no "]:
        assert is_negative(word), f"{word!r} should be negative"


def test_is_affirmative_rejects_unrelated_text():
    assert not is_affirmative("what is my balance")


def test_is_negative_rejects_unrelated_text():
    assert not is_negative("what is my balance")


def test_word_lists_do_not_overlap():
    from app.agent.reply_words import CONFIRM_WORDS, CANCEL_WORDS
    assert CONFIRM_WORDS.isdisjoint(CANCEL_WORDS)
