# Architecture — Acme Corp

## Current state

Acme runs a hybrid environment:
- **Compute:** AWS (us-east-1 primary, us-west-2 DR)
- **CI/CD:** GitLab (self-hosted)
- **Source control:** GitLab + GitHub mix; GitHub adoption is recent (last 18 months)
- **AI tooling:** GitHub Copilot enterprise (3,200+ seats)
- **Data platform:** Snowflake + Databricks

## Proposed state (post-engagement)

- Add Azure as a second cloud for the agent stack specifically
- Migrate code-generation agents to GitHub Actions for tighter Copilot integration
- Keep AWS as primary compute for the rest of the platform
- Add per-agent identity management via Entra Agent ID (preview)

## The agentic SDLC pipeline

This is what Stream 1 is building toward:

1. **Intake:** Jira / Linear ticket arrives, classified by an Analyst agent
2. **Spec:** Solution Architect agent produces a TDD from the ticket + existing codebase context
3. **Code:** Coding agent (GitHub Copilot CLI in agentic mode) implements against the TDD
4. **Validate:** Test-runner agent + human-in-the-loop reviewer
5. **Ship:** PR opened via GitHub API, normal review process

## Open architecture questions

- **Context passing between agents:** how does the Analyst agent's reasoning reach the Coding agent? Currently no clean answer.
- **Per-agent identity:** how do we audit which agent did what? Entra Agent ID looks promising but not GA.
- **Data residency:** Acme requires US-only Azure regions. Foundry data zone GA timeline unclear.
- **Cost attribution:** which team's budget pays for an agent run? Need a chargeback model.

## Decisions made

- **Decision (2026-06-10):** Agent runtime = GitHub Copilot CLI (not Claude Code or Codex) — primary reason: existing seat licenses + governance integration
- **Decision (2026-06-08):** Source control = GitHub for the agent stack only; rest stays on GitLab
- **Decision (2026-05-28):** No build of a custom orchestration framework. Use off-the-shelf (Agent 365 when GA, LangGraph in the interim)
