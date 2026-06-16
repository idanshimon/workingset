# templates/

Drop-in templates for wiring `workingset` into common agent surfaces.

Referenced by [`AGENTS.md`](../AGENTS.md) Step 6. Each template carries
placeholder tokens (`<VAULT_PATH>`, `<BRANCH>`, etc.) listed at the top
of the file. Substitute and write the result into the appropriate
location for your agent.

## Files

| File | For | Target location on user's machine |
|---|---|---|
| [`hermes-skill.md`](hermes-skill.md) | Hermes Agent | `~/.hermes/skills/<namespace>/workingset/SKILL.md` |
| [`claude-code-snippet.md`](claude-code-snippet.md) | Claude Code | append to project `CLAUDE.md` |
| [`codex-snippet.md`](codex-snippet.md) | OpenAI Codex CLI | append to project `AGENTS.md` |
| [`cursor-rules.md`](cursor-rules.md) | Cursor / Windsurf | append to `.cursor/rules` or `.windsurfrules` |
| [`cron-refresh.sh`](cron-refresh.sh) | cron / launchd | `~/.local/bin/refresh-briefs.sh` (chmod +x) |

## Placeholder reference

All templates use these placeholders; substitute before installing:

| Placeholder | Meaning | Example |
|---|---|---|
| `<VAULT_PATH>` | Absolute path to the user's vault | `/Users/alice/notes` |
| `<BRANCH>` | A representative branch slug under the vault | `cust/acme` |
| `<WS_PATH>` | Absolute path to the `ws` executable | `/Users/alice/.venv/bin/ws` |
| `<NAMESPACE>` | Optional skill namespace for Hermes | `personal`, `work`, etc. |

## When templates aren't enough

If the user's agent surface isn't listed here, write a new template using
[`hermes-skill.md`](hermes-skill.md) as a structural reference and open a
PR. Templates should be tested against a real install before they ship.
