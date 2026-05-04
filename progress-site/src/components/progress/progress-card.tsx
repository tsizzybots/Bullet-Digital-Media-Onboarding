import { STATUS_CONFIG, type ProgressTask } from '@/types/progress';

interface ProgressCardProps {
  task: ProgressTask;
  onClick: () => void;
}

export function ProgressCard({ task, onClick }: ProgressCardProps) {
  const cfg = STATUS_CONFIG[task.statusKey];
  const isDone = task.statusKey === 'tested_done' || task.statusKey === 'completed';

  return (
    <button
      onClick={onClick}
      className="group w-full rounded-lg border border-border/50 bg-surface-card p-3 text-left transition-all hover:border-border hover:bg-surface-elevated"
    >
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-mono text-[11px] font-medium text-muted-foreground">
          {task.taskCode}
        </span>
        {task.sprint && (
          <span className="text-[10px] text-muted-foreground/50">
            S{task.sprint}
          </span>
        )}
      </div>

      <p className={`text-sm font-medium leading-snug ${isDone ? 'text-muted-foreground' : 'text-foreground'}`}>
        {task.cleanTitle}
      </p>

      <div className="mt-2">
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${cfg.bg} ${cfg.text}`}>
          {isDone && (
            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
            </svg>
          )}
          {cfg.label}
        </span>
      </div>
    </button>
  );
}
