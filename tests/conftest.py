from types import MappingProxyType

from kpi_extractor.pipeline import SymbolPipeline


def _set_values(self, rows):
    self.store._values.clear()
    self.store._values.update({key: MappingProxyType(dict(row)) for key, row in rows.items()})


def _set_filings(self, rows):
    self.store._filings.clear()
    self.store._filings.update(rows)


# Tests set up a company's stored rows directly; a run only loads them from the control plane.
SymbolPipeline.values = property(lambda self: self.store.values, _set_values)
SymbolPipeline.filings = property(lambda self: self.store.filings, _set_filings)
