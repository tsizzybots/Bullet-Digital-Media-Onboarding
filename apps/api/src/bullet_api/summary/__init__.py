"""Sales-call summarisation (S1-29).

`models` holds the PRD section 7.1 structured-summary contract (`SalesCallSummary`),
`prompts` holds the byte-stable system prompt + few-shot examples used as the
cached prompt prefix. The LLM seam itself lives in `bullet_api.worker.summary_client`.
"""

from __future__ import annotations
