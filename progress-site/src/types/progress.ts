export interface BoardNote {
  content: string;
  author: string;
  created_at: string;
}

export interface BoardCard {
  id: string;
  title: string;
  description: string;
  status: string | null;
  list_name: string | null;
  tags: string[];
  notes: BoardNote[];
  created_at: string;
  updated_at: string;
}

export interface BoardList {
  name: string;
  cards: BoardCard[];
}

export interface BoardSnapshot {
  generated_at: string;
  board_name: string;
  lists: BoardList[];
}

/** Parsed card with extracted metadata */
export interface ProgressTask {
  id: string;
  taskCode: string;       // e.g. "S1-06"
  cleanTitle: string;     // e.g. "Schema migration - clients table"
  sprint: number | null;  // e.g. 1
  sprintLabel: string;    // e.g. "Sprint 1"
  statusKey: StatusKey;
  description: string;
  notes: BoardNote[];
  tags: string[];
  updatedAt: string;
}

export type StatusKey =
  | 'tested_done'
  | 'completed'
  | 'to_review'
  | 'in_progress'
  | 'next_up'
  | 'planned'
  | 'backlog';

export interface StatusConfig {
  label: string;
  bg: string;
  text: string;
  sortOrder: number;
}

export const STATUS_CONFIG: Record<StatusKey, StatusConfig> = {
  tested_done: { label: 'Tested & Done', bg: 'bg-zone-green/15', text: 'text-zone-green', sortOrder: 0 },
  completed:   { label: 'Completed',     bg: 'bg-brand-500/15',  text: 'text-brand-400',  sortOrder: 1 },
  to_review:   { label: 'To Review',     bg: 'bg-purple-500/15', text: 'text-purple-400', sortOrder: 2 },
  in_progress: { label: 'In Progress',   bg: 'bg-zone-amber/15', text: 'text-zone-amber', sortOrder: 3 },
  next_up:     { label: 'Next Up',       bg: 'bg-muted-foreground/15', text: 'text-muted-foreground', sortOrder: 4 },
  planned:     { label: 'Planned',       bg: 'bg-muted/15',            text: 'text-muted-foreground', sortOrder: 5 },
  backlog:     { label: 'Backlog',       bg: 'bg-secondary/20',        text: 'text-muted-foreground', sortOrder: 6 },
};

export const SPRINT_LABELS: Record<number, string> = {
  1: 'Foundation & Signing Capture',
  2: 'Core Fan-Out',
  3: 'Financial, Comms & Kick-Off',
  4: 'Sales Intelligence, Polish & Pilot',
};

const LIST_TO_STATUS: Record<string, StatusKey> = {
  'Backlog': 'backlog',
  'Planned': 'planned',
  'Sprint 1: Foundation & Signing Capture': 'planned',
  'Sprint 2: Core Fan-Out': 'planned',
  'Sprint 3: Financial, Comms & Kick-Off': 'planned',
  'Sprint 4: Sales Intelligence, Polish & Pilot': 'planned',
  'Next Up': 'next_up',
  'In Progress': 'in_progress',
  'To Review': 'to_review',
  'Completed': 'completed',
  'Tested & Done': 'tested_done',
};

export function parseCard(card: BoardCard, listName?: string): ProgressTask {
  const title = card.title;
  let taskCode = '';
  let cleanTitle = title;
  let sprint: number | null = null;

  // Bullet pattern: "S{N}-{NN}: Human Readable Title"
  const colonMatch = title.match(/^(S(\d+)-\w+):\s*(.+)$/);
  // Fallback for "S{N}-hyphenated-title" (shouldn't appear in this board, but parse defensively)
  const hyphenMatch = title.match(/^(S(\d+)-\w+)-(.+)$/);

  if (colonMatch) {
    taskCode = colonMatch[1];
    sprint = parseInt(colonMatch[2], 10);
    cleanTitle = colonMatch[3].trim();
  } else if (hyphenMatch) {
    taskCode = hyphenMatch[1];
    sprint = parseInt(hyphenMatch[2], 10);
    cleanTitle = hyphenMatch[3].replace(/-/g, ' ').trim();
  }

  const resolvedList = listName ?? card.list_name ?? '';
  const statusKey = LIST_TO_STATUS[resolvedList] ?? 'backlog';

  return {
    id: card.id,
    taskCode,
    cleanTitle,
    sprint,
    sprintLabel: sprint ? `Sprint ${sprint}` : 'Unscheduled',
    statusKey,
    description: card.description,
    notes: card.notes,
    tags: card.tags,
    updatedAt: card.updated_at,
  };
}
