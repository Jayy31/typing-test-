from datetime import datetime, timedelta

from app.config import IST
from app.processing.entity_map import EntityMapper, InstrumentInfo, Mapping
from app.processing.relevance import decide

INSTS = [
    InstrumentInfo("BHARTIARTL", "Bharti Airtel", "Telecom", ("Airtel", "Bharti Airtel")),
    InstrumentInfo("SBIN", "State Bank of India", "Banks", ("SBI", "State Bank of India")),
    InstrumentInfo("SAIL", "Steel Authority of India", "Metals", ("SAIL",)),
    InstrumentInfo("OIL", "Oil India", "Oil & Gas", ("Oil India",)),
    InstrumentInfo("LT", "Larsen & Toubro", "Capital Goods", ("L&T", "Larsen & Toubro")),
    InstrumentInfo("IDEA", "Vodafone Idea", "Telecom", ("Vodafone Idea", "Vi")),
]


def mapper() -> EntityMapper:
    return EntityMapper(INSTS)


def test_maps_aliases_sectors_and_themes() -> None:
    m = mapper().map("Airtel and SBI gain as crude oil prices fall; RBI cuts repo rate")
    assert m.symbols == ["BHARTIARTL", "SBIN"]
    assert m.sectors == ["Banks", "Telecom"]
    assert {"crude", "rates"} <= set(m.themes)


def test_ambiguous_words_do_not_match() -> None:
    m = mapper().map("Ships sail on as oil flows; he said vi is a text editor")
    assert m.symbols == []


def test_ampersand_names_and_boundaries() -> None:
    assert mapper().map("L&T bags mega order").symbols == ["LT"]
    assert mapper().map("Oil India raises output").symbols == ["OIL"]
    assert mapper().map("SBIN is not SBINX").symbols == ["SBIN"]


def test_relevance_gate() -> None:
    now = datetime.now(IST)
    hit = Mapping(["BHARTIARTL"], ["Telecom"], [])
    assert decide("Airtel announces tariff hike across plans", hit, now).relevant
    assert not decide("Weather is nice today in Mumbai city", Mapping([], [], []), now).relevant
    assert not decide("Stocks to watch: Airtel, SBI today", hit, now).reason == "ok"
    # fact trigger rescues a filler-pattern title
    assert decide("Stocks to watch: Airtel announces acquisition", hit, now).relevant
    assert not decide("Airtel hikes tariffs", hit, now - timedelta(days=5)).relevant
    assert not decide("Short", hit, now).relevant
