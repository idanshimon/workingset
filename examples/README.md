# Example vault

This is a canonical fixture vault that ships with workingset to help you
see what a real brief looks like without needing to point the tool at
sensitive data.

It mimics the shape of a customer-notes vault (the shape workingset was
originally designed for), but uses entirely fictional content. Acme Corp,
its stakeholders, and all dates / numbers are invented.

## Try it

From the workingset repo root:

```bash
cd examples/example-vault
ws init                              # one-time index build
ws diff cust/acme                    # measure naive-load cost vs brief cost
ws brief cust/acme --write           # produce cust/acme/brief.md
cat cust/acme/brief.md               # inspect what workingset extracted
ws query "renewal date" -b cust/acme # ranked search within the branch
```

## What's here

```
example-vault/
└── cust/
    └── acme/
        └── context/
            ├── index.md         (highest-signal — has the latest STATUS block)
            ├── personas.md      (8 stakeholders, varied seniority + roles)
            ├── architecture.md  (current + proposed state, decisions, open Qs)
            ├── opp-history.md   (5 meetings over ~3 weeks)
            └── open-items.md    (~20 action items split owner/customer/questions)
```

## Why this shape

The example mirrors the structural patterns workingset extracts on:

- **STATUS blocks** with `## 🔥 STATUS (date)` headers — these are highest-density
  and get pulled verbatim into the brief
- **Owner-tagged checkboxes** — `[ ] **[Name]** ...` — extracted into the
  open-items section of the brief
- **Decision lines** with explicit `Decision:` / `Action:` / `Owner:` /
  `Blocker:` prefixes — extracted into the decisions section
- **Multiple notes** so the topic-headings round-robin actually has notes
  to round-robin across (no single sprawling note hogs the budget)

If you apply workingset to a vault with a different shape (engineering wiki,
research notes, etc.), the budget heuristic still works but you may want to
adjust which sections of the brief have the highest weight.
See [`docs/architecture.md`](../../docs/architecture.md) for the SectionBudget
defaults and how to override them.

## Reproducible test data

These files are also used by `tests/test_integration.py` to exercise the
full pipeline against a known-good fixture. Don't delete them or the
integration test will fail.
