from datetime import datetime
from typing import Protocol

from widegold.schemas.resilience import FactorInput


class FactorProvider(Protocol):
    provider_name: str

    def fetch_factor(self, factor_id: str, as_of: datetime) -> FactorInput:
        ...
