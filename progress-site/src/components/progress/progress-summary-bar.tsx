import { STATUS_CONFIG, type StatusKey, type ProgressTask } from '@/types/progress';

interface ProgressSummaryBarProps {
  tasks: ProgressTask[];
}

const BAR_ORDER: StatusKey[] = [
  'tested_done', 'completed', 'to_review', 'in_progress',
  'next_up', 'planned', 'backlog',
];

const BAR_COLORS: Record<StatusKey, string> = {
  tested_done: 'bg-zone-green',
  completed: 'bg-brand-500',
  to_review: 'bg-purple-500',
  in_progress: 'bg-zone-amber',
  next_up: 'bg-muted-foreground',
  planned: 'bg-muted',
  backlog: 'bg-secondary',
};

export function ProgressSummaryBar({ tasks }: ProgressSummaryBarProps) {
  const total = tasks.length;
  const counts = BAR_ORDER.reduce(
    (acc, key) => {
      acc[key] = tasks.filter((t) => t.statusKey === key).length;
      return acc;
    },
    {} as Record<StatusKey, number>,
  );

  const doneCount = counts.tested_done + counts.completed;

  return (
    <div className="space-y-3">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-secondary">
        {BAR_ORDER.map((key) => {
          const pct = total > 0 ? (counts[key] / total) * 100 : 0;
          if (pct === 0) return null;
          return (
            <div
              key={key}
              className={`${BAR_COLORS[key]} transition-all duration-500`}
              style={{ width: `${pct}%` }}
              title={`${STATUS_CONFIG[key].label}: ${counts[key]}`}
            />
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="font-medium text-foreground">
          {doneCount}/{total} done
        </span>
        {BAR_ORDER.map((key) => {
          if (counts[key] === 0) return null;
          return (
            <span key={key} className="flex items-center gap-1.5 text-muted-foreground">
              <span className={`inline-block h-2 w-2 rounded-full ${BAR_COLORS[key]}`} />
              {STATUS_CONFIG[key].label} ({counts[key]})
            </span>
          );
        })}
      </div>
    </div>
  );
}
