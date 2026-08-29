---
name: semantic-reheating
description: Use when an agent repeats work without progress, faces uncertain writes, loses authority, or reaches a declared budget.
---

# Semantic Reheating

Act on observed state; the host owns every external effect.

## Decide

| Observed state | Advisory action |
| --- | --- |
| Repetition **and** no progress | `diagnose` or `reheat` |
| Repetition with new evidence or new pages | `continue` |
| External authority without delegation | `escalate` or `stop` |
| Unknown outcome for a non-idempotent write | `stop` or `escalate`; never retry it |
| Any declared budget exhausted | `stop` |

## Rules

1. Diagnose before changing course: name the repeated operation and the missing progress evidence. Do not treat repetition alone as stagnation.
2. Reheat only a bounded, read-only plan or context. Keep the host in control of tools, writes, retries, and delegation.
3. Productive pagination is not a retry loop. Continue while each page supplies new evidence and budget remains.
4. An external owner or missing delegation is an authority boundary. Escalate or stop; do not perform the change.
5. An unknown non-idempotent write may already have happened. Do not execute or retry it; ask the host to verify or escalate.
6. Treat an exhausted turn, tool, token, time, or cost budget as a hard stop.

Use the public pressure inputs and decision checks in [references/pressure-scenarios.json](references/pressure-scenarios.json) and [references/rubric.json](references/rubric.json).
