"""quality.reasons: one place says what a figure's notes mean; every check decides by these codes."""
from kpi_extractor.quality.reasons import ASIDES, REASONS, codes_of, has, is_, note_with, only, without


def test_each_wording_the_steps_write_has_its_code():
    written = {
        "changed 3.1x in a quarter": "jump", "moved +26 in a quarter": "jump", "moved -30.5 points in a quarter": "jump",
        "the same figure as a year earlier: check the column": "same_as_earlier",
        "amount far from its other periods or from revenue: a scale not stated": "scale_outlier",
        "scale not stated in the document": "scale_not_stated",
        "fewer rows and under half the total of the quarter compared: likely part of the table": "part_of_table",
        "read by one OCR model only (DeepSeek)": "one_ocr_reader",
        "a new reading of this filing differs from the figure served before: 6,110,000,000": "reread_differs",
        "parts differ from the total by -3.1%": "parts_differ", "another part of this breakdown needs review": "other_part",
        "total matches no figure the company reported in XBRL": "no_xbrl_total",
        "equals a figure reported in XBRL": "equals_xbrl", "restated in the next year's release (was 12.5)": "restated_later",
        "the 10-Q's exact figure; every line of the quarter is tagged": "tagged_exact",
        "confirmed by review": "reviewed", "checked against 0000004962-25-000007": "checked",
        "change explained: up 40% on new subscribers": "explained",
    }
    assert {note: codes_of([note])[0] for note in written} == written


def test_the_owners_moved_note_is_no_jump():
    # The jump test once matched any note starting "moved ", the owner's re-filing note among them.
    assert not is_("moved from Other Spread Products by the owner", "jump")
    assert not is_("changed its name", "jump")


def test_display_text_has_no_code_and_still_counts_as_a_hold_where_a_check_asks_for_only_some():
    notes = ["changed 3.1x in a quarter", "scale quoted: 'in thousands'"]
    assert codes_of(notes) == ["jump", None]
    assert not only(notes, ["jump"])  # an unknown note never clears a figure
    assert only(["changed 3.1x in a quarter", "confirmed by review"], ["jump"], ignoring=ASIDES)
    assert not only(["confirmed by review"], ["jump"], ignoring=ASIDES)  # nothing left: not held for a jump at all
    assert not only([], ["jump"])


def test_helpers_keep_the_notes_text():
    notes = ["restated in the next year's release (was 12.5)", "confirmed by review"]
    assert note_with(notes, "restated_later") == notes[0]
    assert without(notes, "reviewed") == notes[:1]
    assert has(notes, "reviewed", "jump") and not has(None, "reviewed")


def test_codes_are_unique_and_every_reason_has_a_kind():
    assert all(reason.kind in ("hold", "group", "proof", "aside") for reason in REASONS.values())
