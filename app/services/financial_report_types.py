"""Immutable report presentation objects shared by current reports and exports."""
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime

NOTICE = 'Operational report only. It is not a tax invoice, a receipt or a legal accounting statement.'

TEXT = 'text'

AMOUNT = 'amount'

COUNT = 'count'

MOMENT = 'moment'

Column = namedtuple('Column', 'key label kind')

@dataclass(frozen=True)
class ReportSection:
    """One table of a report. ``rows`` and ``footer`` map a column key to a
    ``str``, an ``int``, an exact ``Decimal`` or a naive center-local
    ``datetime`` -- never to an internal id."""
    key: str
    title: str
    description: str
    columns: tuple
    rows: tuple
    footer: dict
    empty_text: str
    paginated: bool = False

@dataclass(frozen=True)
class FinancialReport:
    key: str
    title: str
    filters: tuple
    generated_local: datetime
    tz_name: str
    currency_code: str
    notice: str
    notes: tuple
    sections: tuple
