"""Entity mapper: text -> NSE symbols, sectors and macro themes (keyword pass, no AI)."""

import re
from dataclasses import dataclass, field

from app import taxonomy


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    sector: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Mapping:
    symbols: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)


class EntityMapper:
    """Names match case-sensitively, as headlines write them ('Airtel', 'SBI', 'L&T'), so
    ordinary words like 'sail', 'oil' or 'vi' never match a company. The AI tagger resolves
    what remains ambiguous."""

    def __init__(self, instruments: list[InstrumentInfo]) -> None:
        blocked = taxonomy.ambiguous_aliases()
        self._themes = taxonomy.theme_keywords()
        self._sector_of = {i.symbol: i.sector for i in instruments}
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for inst in instruments:
            names = {inst.symbol, inst.name, *inst.aliases}
            for name in names:
                if not name or name.lower() in blocked:
                    continue
                pat = re.compile(rf"(?<![A-Za-z0-9&]){re.escape(name)}(?![A-Za-z0-9&])")
                self._patterns.append((inst.symbol, pat))

    def map(self, text: str) -> Mapping:
        symbols = sorted({sym for sym, pat in self._patterns if pat.search(text)})
        sectors = sorted({self._sector_of[s] for s in symbols})
        low = text.lower() + " "
        themes = sorted(t for t, words in self._themes.items() if any(w in low for w in words))
        return Mapping(symbols, sectors, themes)
