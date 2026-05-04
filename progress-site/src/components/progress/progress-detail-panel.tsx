import { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { STATUS_CONFIG, type ProgressTask } from '@/types/progress';

interface ProgressDetailPanelProps {
  task: ProgressTask | null;
  onClose: () => void;
}

function formatDate(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function ProgressDetailPanel({ task, onClose }: ProgressDetailPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const open = !!task;

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  const cfg = task ? STATUS_CONFIG[task.statusKey] : null;

  return (
    <AnimatePresence>
      {open && task && cfg && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
            onClick={onClose}
          />

          <motion.div
            ref={panelRef}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col bg-black shadow-2xl"
          >
            <div className="flex items-start justify-between border-b border-border px-6 py-4">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center gap-2">
                  <span className="font-mono text-xs font-medium text-muted-foreground">
                    {task.taskCode}
                  </span>
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${cfg.bg} ${cfg.text}`}>
                    {cfg.label}
                  </span>
                </div>
                <h2 className="text-lg font-semibold text-foreground">
                  {task.cleanTitle}
                </h2>
              </div>
              <button
                onClick={onClose}
                className="ml-4 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4">
              <div className="mb-4 flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span>{task.sprintLabel}</span>
                {task.tags.length > 0 && (
                  <>
                    <span className="text-border">|</span>
                    {task.tags.map((tag) => (
                      <span key={tag} className="rounded bg-secondary px-1.5 py-0.5 text-muted-foreground">
                        {tag}
                      </span>
                    ))}
                  </>
                )}
                {task.updatedAt && (
                  <>
                    <span className="text-border">|</span>
                    <span>Updated {formatDate(task.updatedAt)}</span>
                  </>
                )}
              </div>

              {task.description && (
                <div className="mb-6">
                  <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Description
                  </h3>
                  <div className="whitespace-pre-wrap rounded-lg bg-surface-card p-4 text-sm leading-relaxed text-secondary-foreground">
                    {task.description}
                  </div>
                </div>
              )}

              {task.notes.length > 0 && (
                <div>
                  <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Activity ({task.notes.length})
                  </h3>
                  <div className="space-y-3">
                    {task.notes.map((note, i) => (
                      <div
                        key={i}
                        className="relative border-l-2 border-border pl-4"
                      >
                        <div className="absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-muted" />
                        <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                          <span className="font-medium text-muted-foreground">
                            {note.author || 'system'}
                          </span>
                          {note.created_at && (
                            <span>{formatDate(note.created_at)}</span>
                          )}
                        </div>
                        <div className="whitespace-pre-wrap text-sm leading-relaxed text-secondary-foreground">
                          {note.content}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {!task.description && task.notes.length === 0 && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No details or activity notes for this task.
                </p>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
