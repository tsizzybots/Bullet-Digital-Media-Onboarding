import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import snapshot from '@/data/board-snapshot.json';
import type { BoardSnapshot } from '@/types/progress';
import { ProgressClient } from '@/App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ProgressClient snapshot={snapshot as BoardSnapshot} />
  </StrictMode>,
);
