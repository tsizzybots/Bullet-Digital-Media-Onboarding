import type { StatusKey } from '@/types/progress';

export type GroupBy = 'sprint' | 'status';
export type StatusFilter = 'all' | 'done' | 'active' | 'upcoming';

interface ProgressFiltersProps {
  groupBy: GroupBy;
  onGroupByChange: (g: GroupBy) => void;
  statusFilter: StatusFilter;
  onStatusFilterChange: (f: StatusFilter) => void;
}

const GROUP_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'sprint', label: 'By Sprint' },
  { value: 'status', label: 'By Status' },
];

const FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'active', label: 'Active' },
  { value: 'upcoming', label: 'Upcoming' },
];

export const FILTER_STATUS_MAP: Record<StatusFilter, StatusKey[] | null> = {
  all: null,
  done: ['tested_done', 'completed'],
  active: ['to_review', 'in_progress'],
  upcoming: ['next_up', 'planned', 'backlog'],
};

export function ProgressFilters({
  groupBy,
  onGroupByChange,
  statusFilter,
  onStatusFilterChange,
}: ProgressFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-4">
      <div className="flex rounded-lg bg-secondary p-0.5">
        {GROUP_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => onGroupByChange(opt.value)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              groupBy === opt.value
                ? 'bg-surface-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="h-5 w-px bg-border" />

      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Filter:</span>
        {FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => onStatusFilterChange(opt.value)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              statusFilter === opt.value
                ? 'bg-brand-500/20 text-brand-400'
                : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
