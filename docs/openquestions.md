# Open Questions for Bullet

Tracked here: anything Bullet (John / Stephen / Steve) must answer before we can finalise an **implementation** decision.

This file is the sister doc to `docs/phase-1-plan.md` Section 11 (which captures plan-scope questions). Use this file for anything blocking a tech / build decision.

Append new questions to the bottom of "Open Questions" with the next free `Q-NN` id. When a question is answered, fill in the **Answer** field, set **Status** to `Answered`, and move the entry to the "Resolved" section at the bottom.

## Format

- **Q-NN**: Title
  - **Asked**: DD/MM/YYYY (or "not yet")
  - **Owner at Bullet**: name
  - **Status**: Open / Awaiting reply / Answered / Withdrawn
  - **Blocks**: which sprint / decision / file
  - **Question (verbatim, as we'll send it)**: ...
  - **Why it matters**: ...
  - **Provisional default if no answer**: ...
  - **Answer (when received)**: ...

---

## Open Questions

### Q-01: Outbound email provider - single system mailbox or per-AM Gmail?

- **Asked**: not yet
- **Owner at Bullet**: Stephen Taylor
- **Status**: Open
- **Blocks**: Sprint 2 (kick-off follow-up email generator); Sprint 3 (technical-requirements email replacement)
- **Question (verbatim)**: When the AI-drafted kick-off follow-up email and the technical-requirements email go out to clients, is it acceptable for them to be sent from a single system mailbox (e.g. `onboarding@bulletdigitalmedia.com`) via a transactional email service (Resend), or must each email be sent from the individual Account Manager's actual Gmail mailbox so the client can reply directly to that specific person and the reply threads in their normal inbox?
- **Why it matters**: Drives whether we build a Gmail-API delegated-send integration (one setup ritual per Account Manager, repeated for every new joiner) or a single Resend integration (one config, never repeated). Also drives reply handling: with Resend we can route replies to a shared inbox or back to the AM via routing rules, but it is not a Gmail thread on the AM's account.
- **Provisional default if no answer**: Resend with a single system mailbox. Reasoning: scales with team growth, gives better deliverability and observability, no per-AM setup ritual every time someone new joins Bullet.
- **Answer**: -

---

## Resolved

(none yet)
