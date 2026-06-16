# Append this block to .cursor/rules (Cursor) or .windsurfrules (Windsurf).
# Placeholders to substitute: <VAULT_PATH>, <BRANCH>, <WS_PATH>

When loading context from the markdown vault at `<VAULT_PATH>`:

1. ALWAYS read `<VAULT_PATH>/<branch>/brief.md` first. The brief is
   pre-computed by [workingset](https://github.com/idanshimon/workingset)
   and is ~30x smaller than the source files it summarizes (~1.7K
   tokens vs ~57K).

2. Only read source files (`<VAULT_PATH>/<branch>/context/*.md`) when
   the brief explicitly says "Not in this context" or the user asks for
   verbatim historical content.

3. To refresh the brief after vault changes:
   ```
   <WS_PATH> brief <branch> --budget 8000 --write
   ```

4. To search across the whole vault instead of reading the brief:
   ```
   <WS_PATH> query "your terms" --branch <branch> --budget 4000
   ```

5. Brief contents (predictable shape):
   - YAML frontmatter
   - Latest STATUS block (verbatim)
   - Open action items (top N, owner-tagged)
   - Recent decisions / owners / blockers
   - Topic headings (round-robin across recent notes)
   - Most-recent notes (titles + paths)

   Every line carries `_[source-path]_` provenance for fall-back.

6. Safety: workingset briefs are verified for frontier-tier models
   (Opus / Sonnet / GPT-5+) at 0% hallucination on out-of-brief
   questions. Smaller models occasionally invent answers; if your
   model is Haiku / mini / similar, treat brief outputs as advisory.

Install + full details: https://github.com/idanshimon/workingset/blob/main/AGENTS.md
