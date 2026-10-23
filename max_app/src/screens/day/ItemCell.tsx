import { CellSimple } from '@maxhub/max-ui';
import type { Item, ItemKind } from '../../api/types';
import { itemTimeLabel } from '../../lib/time';

const KIND_LABEL: Record<ItemKind, string> = {
  event: 'Встреча',
  reminder: 'Напоминание',
  task: 'Задача',
};

export function ItemCell({ item }: { item: Item }) {
  const done = item.status === 'done';
  const subtitle = [
    itemTimeLabel(item),
    KIND_LABEL[item.kind],
    item.participants.length > 0 ? `с ${item.participants.join(', ')}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <CellSimple
      data-testid="item"
      height="normal"
      before={<KindIcon kind={item.kind} done={done} />}
      title={<span className={done ? 'item-title item-title--done' : 'item-title'}>{item.title}</span>}
      subtitle={subtitle}
    />
  );
}

function KindIcon({ kind, done }: { kind: ItemKind; done: boolean }) {
  return (
    <span className={`kind-icon kind-icon--${done ? 'done' : kind}`} aria-label={KIND_LABEL[kind]}>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {done ? <path d="M5 12.5l4.5 4.5L19 7.5" /> : ICON_PATHS[kind]}
      </svg>
    </span>
  );
}

const ICON_PATHS: Record<ItemKind, React.ReactNode> = {
  event: (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M4 10h16M9 3v4M15 3v4" />
    </>
  ),
  reminder: (
    <>
      <path d="M6 16V11a6 6 0 0112 0v5l1.5 2h-15z" />
      <path d="M10 20.5a2 2 0 004 0" />
    </>
  ),
  task: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l2.5 2" />
    </>
  ),
};
