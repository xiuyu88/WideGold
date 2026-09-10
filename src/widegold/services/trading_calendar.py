from datetime import date

from widegold.settings.config import cn_calendar_config


class CNTradingCalendar:
    def __init__(self) -> None:
        self.closed_dates = {date.fromisoformat(str(x)) for x in cn_calendar_config().get("closed_dates", [])}

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.closed_dates
