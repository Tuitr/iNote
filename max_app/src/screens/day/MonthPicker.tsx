import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../../api/client';
import {
  addDays,
  addMonths,
  dayLabel,
  dayLabelLong,
  daysBetween,
  monthLabel,
  monthName,
  startOfMonth,
  startOfWeek,
} from '../../lib/time';
import { useItemsRange } from './useItemsRange';

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** Сколько лет показывает сетка годов: 4 ряда по 3. */
const YEARS_PER_PAGE = 12;

interface MonthPickerProps {
  client: ApiClient;
  selected: string;
  today: string;
  onSelect: (date: string) => void;
}

/** Подпись выбранного дня, по нажатию — выпадающий календарь месяца. */
export function MonthPicker({ client, selected, today, onSelect }: MonthPickerProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // Закрываем по тапу мимо и по Escape.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="month-picker" ref={root}>
      <button
        type="button"
        className="month-picker__toggle"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        {dayLabel(selected)}
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={open ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'} />
        </svg>
      </button>
      {open && (
        <Calendar
          client={client}
          selected={selected}
          today={today}
          onSelect={(date) => {
            onSelect(date);
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}

type View = 'days' | 'months' | 'years';

/**
 * Календарь в три уровня: дни месяца → месяцы года → годы.
 * Монтируется только открытым, поэтому месяц грузится и опрашивается лишь пока его видно.
 */
function Calendar({ client, selected, today, onSelect }: MonthPickerProps) {
  const [month, setMonth] = useState(() => startOfMonth(selected));
  const [view, setView] = useState<View>('days');
  const year = Number(month.slice(0, 4));

  if (view === 'months') {
    return (
      <MonthsView
        year={year}
        selected={selected}
        today={today}
        onYear={(value) => setMonth(withYear(month, value))}
        onOpenYears={() => setView('years')}
        onPick={(value) => {
          setMonth(value);
          setView('days');
        }}
      />
    );
  }
  if (view === 'years') {
    return (
      <YearsView
        year={year}
        selected={selected}
        today={today}
        onPick={(value) => {
          setMonth(withYear(month, value));
          setView('months');
        }}
      />
    );
  }
  return (
    <DaysView
      client={client}
      month={month}
      selected={selected}
      today={today}
      onMonth={setMonth}
      onOpenMonths={() => setView('months')}
      onSelect={onSelect}
    />
  );
}

interface DaysViewProps {
  client: ApiClient;
  month: string;
  selected: string;
  today: string;
  onMonth: (month: string) => void;
  onOpenMonths: () => void;
  onSelect: (date: string) => void;
}

function DaysView({ client, month, selected, today, onMonth, onOpenMonths, onSelect }: DaysViewProps) {
  const nextMonth = addMonths(month, 1);
  const { state } = useItemsRange(client, month, nextMonth);
  // Ошибка или загрузка — просто без точек: выбрать день можно и так.
  const busy = new Set(
    state.status === 'ready'
      ? state.days
          .filter((day) => day.items.some((item) => item.status !== 'cancelled'))
          .map((day) => day.date)
      : [],
  );

  // Пустые клетки до первого числа: неделя начинается с понедельника.
  const leading = Array.from({ length: daysBetween(startOfWeek(month), month) });
  const dates: string[] = [];
  for (let date = month; date < nextMonth; date = addDays(date, 1)) dates.push(date);

  return (
    <div className="month-picker__panel" role="dialog" aria-label="Выбор дня">
      <PanelHead
        title={monthLabel(month)}
        titleLabel="Выбрать месяц и год"
        prevLabel="Предыдущий месяц"
        nextLabel="Следующий месяц"
        onTitle={onOpenMonths}
        onPrev={() => onMonth(addMonths(month, -1))}
        onNext={() => onMonth(nextMonth)}
      />
      <div className="month-picker__grid">
        {WEEKDAYS.map((weekday) => (
          <span key={weekday} className="month-picker__weekday" aria-hidden="true">
            {weekday}
          </span>
        ))}
        {leading.map((_, index) => (
          <span key={`empty-${index}`} />
        ))}
        {dates.map((date) => (
          <button
            key={date}
            type="button"
            className={cellClasses('month-picker__day', date === selected, date === today)}
            aria-label={dayLabelLong(date)}
            aria-pressed={date === selected}
            aria-current={date === today ? 'date' : undefined}
            onClick={() => onSelect(date)}
          >
            {Number(date.slice(8, 10))}
            <span className={busy.has(date) ? 'month-picker__dot month-picker__dot--on' : 'month-picker__dot'} />
          </button>
        ))}
      </div>
    </div>
  );
}

interface MonthsViewProps {
  year: number;
  selected: string;
  today: string;
  onYear: (year: number) => void;
  onOpenYears: () => void;
  onPick: (month: string) => void;
}

function MonthsView({ year, selected, today, onYear, onOpenYears, onPick }: MonthsViewProps) {
  const months = Array.from({ length: 12 }, (_, index) => `${year}-${String(index + 1).padStart(2, '0')}-01`);
  return (
    <div className="month-picker__panel" role="dialog" aria-label="Выбор месяца">
      <PanelHead
        title={String(year)}
        titleLabel="Выбрать год"
        prevLabel="Предыдущий год"
        nextLabel="Следующий год"
        onTitle={onOpenYears}
        onPrev={() => onYear(year - 1)}
        onNext={() => onYear(year + 1)}
      />
      <div className="month-picker__grid month-picker__grid--wide">
        {months.map((month) => {
          const isSelected = month === startOfMonth(selected);
          const isCurrent = month === startOfMonth(today);
          return (
            <button
              key={month}
              type="button"
              className={cellClasses('month-picker__cell', isSelected, isCurrent)}
              aria-label={monthLabel(month)}
              aria-pressed={isSelected}
              aria-current={isCurrent ? 'date' : undefined}
              onClick={() => onPick(month)}
            >
              {monthName(month)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface YearsViewProps {
  year: number;
  selected: string;
  today: string;
  onPick: (year: number) => void;
}

function YearsView({ year, selected, today, onPick }: YearsViewProps) {
  // Страница лет, на которую попадает год из заголовка; дальше листается стрелками.
  const [first, setFirst] = useState(() => year - (year % YEARS_PER_PAGE));
  const years = Array.from({ length: YEARS_PER_PAGE }, (_, index) => first + index);
  const selectedYear = Number(selected.slice(0, 4));
  const currentYear = Number(today.slice(0, 4));
  return (
    <div className="month-picker__panel" role="dialog" aria-label="Выбор года">
      <PanelHead
        title={`${first} – ${first + YEARS_PER_PAGE - 1}`}
        prevLabel="Предыдущие годы"
        nextLabel="Следующие годы"
        onPrev={() => setFirst(first - YEARS_PER_PAGE)}
        onNext={() => setFirst(first + YEARS_PER_PAGE)}
      />
      <div className="month-picker__grid month-picker__grid--wide">
        {years.map((value) => (
          <button
            key={value}
            type="button"
            className={cellClasses('month-picker__cell', value === selectedYear, value === currentYear)}
            aria-pressed={value === selectedYear}
            aria-current={value === currentYear ? 'date' : undefined}
            onClick={() => onPick(value)}
          >
            {value}
          </button>
        ))}
      </div>
    </div>
  );
}

interface PanelHeadProps {
  title: string;
  /** Подпись заголовка-кнопки; без onTitle заголовок — просто текст. */
  titleLabel?: string;
  prevLabel: string;
  nextLabel: string;
  onTitle?: () => void;
  onPrev: () => void;
  onNext: () => void;
}

function PanelHead({ title, titleLabel, prevLabel, nextLabel, onTitle, onPrev, onNext }: PanelHeadProps) {
  return (
    <div className="month-picker__head">
      <button type="button" className="month-picker__arrow" aria-label={prevLabel} onClick={onPrev}>
        <Chevron d="M15 5l-7 7 7 7" />
      </button>
      {onTitle ? (
        <button
          type="button"
          className="month-picker__title month-picker__title--button"
          aria-label={`${title}. ${titleLabel}`}
          onClick={onTitle}
        >
          {title}
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
      ) : (
        <span className="month-picker__title">{title}</span>
      )}
      <button type="button" className="month-picker__arrow" aria-label={nextLabel} onClick={onNext}>
        <Chevron d="M9 5l7 7-7 7" />
      </button>
    </div>
  );
}

function cellClasses(base: string, selected: boolean, current: boolean): string {
  const classes = [base];
  if (selected) classes.push(`${base}--selected`);
  if (current) classes.push(`${base}--today`);
  return classes.join(' ');
}

/** Первое число того же месяца в другом году. */
function withYear(month: string, year: number): string {
  return `${year}${month.slice(4)}`;
}

function Chevron({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
