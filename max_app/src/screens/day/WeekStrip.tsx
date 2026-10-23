import type { ItemsDay } from '../../api/types';
import { addDays, dayLabelLong, weekdayShort } from '../../lib/time';

interface WeekStripProps {
  weekStart: string;
  selected: string;
  today: string;
  /** Дни недели с записями — для точек под датой; пока неделя грузится, undefined. */
  days: ItemsDay[] | undefined;
  onSelect: (date: string) => void;
  onShiftWeek: (weeks: number) => void;
}

/** Полоска «Пн–Вс» со стрелками на соседние недели. */
export function WeekStrip({ weekStart, selected, today, days, onSelect, onShiftWeek }: WeekStripProps) {
  const dates = Array.from({ length: 7 }, (_, index) => addDays(weekStart, index));

  return (
    <nav className="week-strip" aria-label="Дни недели">
      <button
        type="button"
        className="week-strip__arrow"
        aria-label="Предыдущая неделя"
        onClick={() => onShiftWeek(-1)}
      >
        <Chevron direction="left" />
      </button>
      {dates.map((date) => {
        const classes = ['week-strip__day'];
        if (date === selected) classes.push('week-strip__day--selected');
        if (date === today) classes.push('week-strip__day--today');
        const hasItems = Boolean(
          days?.find((day) => day.date === date)?.items.some((item) => item.status !== 'cancelled'),
        );
        return (
          <button
            key={date}
            type="button"
            className={classes.join(' ')}
            aria-label={dayLabelLong(date)}
            aria-pressed={date === selected}
            aria-current={date === today ? 'date' : undefined}
            onClick={() => onSelect(date)}
          >
            <span className="week-strip__weekday">{weekdayShort(date)}</span>
            <span className="week-strip__number">{Number(date.slice(8, 10))}</span>
            <span className={hasItems ? 'week-strip__dot week-strip__dot--on' : 'week-strip__dot'} />
          </button>
        );
      })}
      <button
        type="button"
        className="week-strip__arrow"
        aria-label="Следующая неделя"
        onClick={() => onShiftWeek(1)}
      >
        <Chevron direction="right" />
      </button>
    </nav>
  );
}

function Chevron({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === 'left' ? 'M15 5l-7 7 7 7' : 'M9 5l7 7-7 7'} />
    </svg>
  );
}
