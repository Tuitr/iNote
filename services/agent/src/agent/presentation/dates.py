"""Даты и время для ответа пользователю — по-русски, в его поясе, относительно его «сегодня»."""

from datetime import date, datetime, timedelta

from agent.presentation.templates import DateTemplates


# Примечание: форматирование дат по шаблонам; today — «сегодня» пользователя в его поясе
class DateFormatter:
    # Примечание: шаблоны дат и «сегодня» — от часов хода
    def __init__(self, templates: DateTemplates, today: date) -> None:
        self._t = templates
        self._today = today

    # Примечание: «ср 21 октября»; год — только если он не текущий
    def day(self, value: date) -> str:
        fields = {
            "weekday": self._t.weekdays[value.weekday()],
            "day": value.day,
            "month": self._t.months[value.month - 1],
            "year": value.year,
        }
        pattern = self._t.day if value.year == self._today.year else self._t.dayWithYear
        return pattern.format(**fields)

    # Примечание: «Сегодня, ср 21 октября» / «Завтра, …» / «Вчера, …»; иначе просто день
    def relativeDay(self, value: date) -> str:
        relative = {
            self._today: self._t.today,
            self._today + timedelta(days=1): self._t.tomorrow,
            self._today - timedelta(days=1): self._t.yesterday,
        }.get(value)
        if relative is None:
            return self.day(value)
        return self._t.relativeDay.format(relative=relative, day=self.day(value))

    # Примечание: «Завтра, чт 22 октября, 10:00»; весь день — без времени
    def moment(self, value: datetime, *, allDay: bool) -> str:
        day = self.relativeDay(value.date())
        if allDay:
            return day
        return self._t.moment.format(day=day, time=clock(value))

    # Примечание: «10:00–11:00» или «10:00», если конца нет или он в другой день
    def timeSpan(self, start: datetime, end: datetime | None) -> str:
        if end is None or end.date() != start.date() or end <= start:
            return clock(start)
        return self._t.timeRange.format(start=clock(start), end=clock(end))

    # Примечание: «19–25 октября» или «26 октября – 1 ноября»; first и last включительно
    def weekRange(self, first: date, last: date) -> str:
        if first.month == last.month:
            month = self._t.months[first.month - 1]
            return self._t.weekSameMonth.format(first=first.day, last=last.day, month=month)
        return self._t.weekTwoMonths.format(
            first=first.day,
            first_month=self._t.months[first.month - 1],
            last=last.day,
            last_month=self._t.months[last.month - 1],
        )


# Примечание: время без секунд: «09:05»
def clock(value: datetime) -> str:
    return f"{value:%H:%M}"
