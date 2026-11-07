from kpi_extractor.ai import propose_data
from kpi_extractor.sectors import sector_hint, sector_of


def test_sic_codes_name_their_industry():
    cases = {6021: "bank", 6022: "bank", 6311: "insurance", 6324: "insurance", 6211: "asset_manager",
             6282: "asset_manager", 6798: "reit", 2911: "energy", 1381: "energy", 4911: "utility", 7372: "software",
             3674: "semis_hardware", 3760: "aerospace_defense", 3711: "auto", 4513: "transport", 5331: "consumer",
             8011: "healthcare", 1000: "materials", 4812: "telecom_media", 8200: "education", 1623: "industrial"}
    assert {code: sector_of(code).key for code in cases} == cases
    # Too broad or unknown: no hint at all.
    assert sector_of(7389) is None and sector_of(700) is None and sector_of(None) is None and sector_of("x") is None


def test_a_hint_is_examples_never_a_list_to_fill():
    hint = sector_hint(sector_of(6021))
    assert "not a list to fill" in hint and "never invent" in hint and "even if it is not named here" in hint
    assert "net interest margin" in hint and 'despite the general "Never track" list' in hint
    assert "despite" not in sector_hint(sector_of(7372))  # software lifts no rule
    assert sector_hint(None) == ""


def test_the_hint_goes_after_the_task_and_only_when_there_is_one():
    assert propose_data([], [], None, "Industry hints (Banks): ...").startswith("Industry hints (Banks)")
    assert propose_data([], []).startswith("Already covered by XBRL")
