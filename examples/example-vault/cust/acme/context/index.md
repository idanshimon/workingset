# Acme Corp — Context Dashboard

> **Vault:** customer-notes example
> **Account TPID:** 999001
> **Generated:** see `brief.md` for the workingset-built summary

## Overview

Acme Corp is a fictional reference customer used as the workingset example
fixture vault. This file mimics the shape of a real "customer-hub" entry:
a STATUS block at the top, structured stakeholder sections, open action
items, and accumulated meeting notes elsewhere in the branch.

If you point `workingset` at the root of this folder, it should produce a
`brief.md` that captures the most-recent STATUS block, the open action
items, recent decisions, and a topic index — without you having to read
all 5 files in this branch.

## 🔥 STATUS (2026-06-16 · 11:18 ET): RENEWAL CALL DONE | SCOPE LOCKED AS AGENTIC SDLC | 8 ATTENDEES | NEXT STEP: PROOF-OF-CONCEPT DESIGN

**Just landed from the renewal call:**
- **Priya corrected scope:** scope is "agentic SDLC" not "platform migration"
- **Stakeholder sign-off:** confirmed by Devon (VP Engineering) and Priya (AVP Platform)
- **8 attendees confirmed** for the in-person POC kickoff
- **Workshop dates locked:** July 14-15 (Atlanta office)
- **Open question:** which CI/CD provider for the POC — Acme uses GitLab today, Devon is open to GitHub Actions for the agent stack

## Stream 1 — Agentic SDLC (Priya Sharma, primary buyer)

- **Priya Sharma** = AVP Platform Engineering, owns the AI control plane
- 3,200-3,500 in-house developers, 2,800+ on GitHub Copilot
- Core ask: per-agent identity, registry, governance — built on top of Copilot CLI
- **90-day target:** Phase 2 production by end of Q3 2026
- Next: schedule deep-dive workshop with Priya's 10-20 core engineers (within 2 weeks)

## Stream 2 — Cloud Migration (Devon Dawson, VP Eng)

- Long-running modernization initiative
- Currently on AWS, evaluating Azure for the agent stack
- Capgemini is the incumbent migration partner (limited scope, soft risk)
- Success metric: measure agent performance vs human engineer baselines (10-20% productivity target)

## Recent decisions

- **Owner:** Devon Dawson / PartnerOps + Lin Ko
- **Decision (2026-06-10):** Workshop day-2 demos REMOVED from agenda; replaced with hands-on labs
- **Blocker (2026-06-09):** Data residency — Acme requires US-only Azure regions; no GA date for foundry data zone
- **Action:** Priya leads the architectural review with vendor team weekly

## Topics covered (recent meeting notes)

- Overview of the agentic stack proposal
- Stakeholder roles and reporting lines
- POC scope: 3 use cases, 6-week timeline
- Security posture for agent identity
- Cost model: per-agent pricing vs flat-rate

## Most recent notes

- `meeting-2026-06-15_renewal-call.md`
- `meeting-2026-06-09_security-posture.md`
- `meeting-2026-06-02_initial-discovery.md`
- `personas.md`
- `architecture.md`
