import { useState, useMemo } from 'react';
import {
  STATUS_CONFIG,
  SPRINT_LABELS,
  parseCard,
  type BoardSnapshot,
  type ProgressTask,
  type StatusKey,
} from '@/types/progress';
import { ProgressSummaryBar } from '@/components/progress/progress-summary-bar';
import {
  ProgressFilters,
  FILTER_STATUS_MAP,
  type GroupBy,
  type StatusFilter,
} from '@/components/progress/progress-filters';
import { ProgressCard } from '@/components/progress/progress-card';
import { ProgressDetailPanel } from '@/components/progress/progress-detail-panel';

interface ProgressClientProps {
  snapshot: BoardSnapshot;
}

interface SprintGroup {
  sprint: number;
  label: string;
  tasks: ProgressTask[];
}

interface StatusGroup {
  statusKey: StatusKey;
  label: string;
  tasks: ProgressTask[];
}

export function ProgressClient({ snapshot }: ProgressClientProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>('sprint');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [selectedTask, setSelectedTask] = useState<ProgressTask | null>(null);

  const allTasks = useMemo(() => {
    const tasks: ProgressTask[] = [];
    for (const list of snapshot.lists) {
      for (const card of list.cards) {
        tasks.push(parseCard(card, list.name));
      }
    }
    return tasks.sort((a, b) => {
      if (a.sprint !== b.sprint) return (a.sprint ?? 99) - (b.sprint ?? 99);
      return a.taskCode.localeCompare(b.taskCode);
    });
  }, [snapshot]);

  const filteredTasks = useMemo(() => {
    const allowed = FILTER_STATUS_MAP[statusFilter];
    if (!allowed) return allTasks;
    return allTasks.filter((t) => allowed.includes(t.statusKey));
  }, [allTasks, statusFilter]);

  const sprintGroups = useMemo((): SprintGroup[] => {
    const map = new Map<number, ProgressTask[]>();
    for (const task of filteredTasks) {
      const s = task.sprint ?? 0;
      if (!map.has(s)) map.set(s, []);
      map.get(s)!.push(task);
    }
    return [...map.entries()]
      .sort(([a], [b]) => a - b)
      .map(([sprint, tasks]) => ({
        sprint,
        label: sprint > 0
          ? `Sprint ${sprint} — ${SPRINT_LABELS[sprint] ?? ''}`
          : 'Unscheduled',
        tasks,
      }));
  }, [filteredTasks]);

  const statusGroups = useMemo((): StatusGroup[] => {
    const map = new Map<StatusKey, ProgressTask[]>();
    for (const task of filteredTasks) {
      if (!map.has(task.statusKey)) map.set(task.statusKey, []);
      map.get(task.statusKey)!.push(task);
    }
    return [...map.entries()]
      .sort(([a], [b]) => STATUS_CONFIG[a].sortOrder - STATUS_CONFIG[b].sortOrder)
      .map(([statusKey, tasks]) => ({
        statusKey,
        label: STATUS_CONFIG[statusKey].label,
        tasks,
      }));
  }, [filteredTasks]);

  const generatedDate = new Date(snapshot.generated_at).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });

  return (
    <div className="min-h-screen bg-black">
      <header className="sticky top-0 z-30 border-b border-border/60 bg-black/90 px-4 py-5 backdrop-blur-md sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-4">
              <img
                src="/izzyagents-white.png"
                alt="IzzyAgents"
                className="h-9"
              />
              <div className="h-5 w-px bg-border" />
              <h1 className="text-lg font-bold text-foreground">
                Bullet Digital Media
                <span className="ml-2 font-normal text-muted-foreground">— Phase 1 Progress</span>
              </h1>
            </div>
            <span className="text-xs text-muted-foreground">
              Last updated: {generatedDate}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="mb-6">
          <ProgressSummaryBar tasks={allTasks} />
        </div>

        <div className="mb-6">
          <ProgressFilters
            groupBy={groupBy}
            onGroupByChange={setGroupBy}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
          />
        </div>

        {groupBy === 'sprint' ? (
          <div className="space-y-8">
            {sprintGroups.map((group) => (
              <SprintSection
                key={group.sprint}
                group={group}
                allTasksInSprint={allTasks.filter((t) => (t.sprint ?? 0) === group.sprint)}
                onSelectTask={setSelectedTask}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-8">
            {statusGroups.map((group) => (
              <StatusSection
                key={group.statusKey}
                group={group}
                onSelectTask={setSelectedTask}
              />
            ))}
          </div>
        )}

        {filteredTasks.length === 0 && (
          <div className="py-16 text-center text-sm text-muted-foreground">
            No tasks match the current filter.
          </div>
        )}
      </main>

      <ProgressDetailPanel
        task={selectedTask}
        onClose={() => setSelectedTask(null)}
      />
    </div>
  );
}

function SprintSection({
  group,
  allTasksInSprint,
  onSelectTask,
}: {
  group: SprintGroup;
  allTasksInSprint: ProgressTask[];
  onSelectTask: (t: ProgressTask) => void;
}) {
  const doneCount = allTasksInSprint.filter(
    (t) => t.statusKey === 'tested_done' || t.statusKey === 'completed',
  ).length;
  const totalCount = allTasksInSprint.length;
  const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  return (
    <section>
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-foreground">{group.label}</h2>
        <span className="text-xs text-muted-foreground">
          {totalCount} tasks
        </span>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-zone-green transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-[11px] text-muted-foreground">{pct}%</span>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {group.tasks.map((task) => (
          <ProgressCard
            key={task.id}
            task={task}
            onClick={() => onSelectTask(task)}
          />
        ))}
      </div>
    </section>
  );
}

function StatusSection({
  group,
  onSelectTask,
}: {
  group: StatusGroup;
  onSelectTask: (t: ProgressTask) => void;
}) {
  const cfg = STATUS_CONFIG[group.statusKey];

  return (
    <section>
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-foreground">{group.label}</h2>
        <span className={`inline-block h-2 w-2 rounded-full ${cfg.bg.replace('/15', '').replace('/20', '')}`} />
        <span className="text-xs text-muted-foreground">
          {group.tasks.length} tasks
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {group.tasks.map((task) => (
          <ProgressCard
            key={task.id}
            task={task}
            onClick={() => onSelectTask(task)}
          />
        ))}
      </div>
    </section>
  );
}
