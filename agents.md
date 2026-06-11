# agents.md

Behavioral guidelines to reduce common LLM coding mistakes, plus project-specific context for the Wearless Studio frontend. Merge with task-specific instructions as needed.

## Project & Architecture

- **Wearless Studio:** an AI detail-page studio web app for fashion e-commerce sellers. Users input product photos and basic info; the AI generates a detail-page draft, images, and copy; users edit and download the result. Product source of truth: `documents/PRD.md`.
- **Vite + React SPA, mock-first.** This is a Vite React app, **not** a Next.js app. Do NOT introduce: App Router, `app/`/`pages/` conventions, Next API routes, Server Actions, `next/image`, or Next middleware.
- **Layer boundary:** the frontend owns UI and state only. Do not call real APIs, Supabase, or AI endpoints directly inside components — go through the service/mock layer.

## Tech Stack

- **Frontend:** Vite + React
- **Language:** JavaScript / JSX first. Do not convert the whole project to TypeScript. Small `.ts` files are allowed only for shared contracts, store types, or API contracts when explicitly requested by the handoff/spec.
- **Styling:** CSS + CSS variables. Use tokens from `tokens.css`. CSS Modules are allowed for component/feature scoping. No inline styles, no CSS-in-JS, no hardcoded hex if a token exists.
- **State:** Zustand
- **Data layer:** mock-first — all data flows through the `mock/` layer (`api.js`, `db.js`, `placeholders.js`) until the backend is wired in.
- **Planned backend (not built yet):** FastAPI on Railway · Supabase (Postgres/Auth) · Cloudflare R2 (object storage)
- **AI pipeline:** see `documents/PRD.md`

## Commands & Testing

- Use existing `package.json` scripts only. Do not invent commands; if a needed one isn't listed, inspect `package.json` first and explain what you found before running anything.
- Run: `pnpm dev` · Build: `pnpm build` · Preview: `pnpm preview`
- Lint / Test: only if a corresponding script exists in `package.json`.
- **No formal test suite yet.** Until one exists, "verify" means: the build passes, lint passes (if configured), and the change is checked manually in `pnpm dev`. For bugs, reproduce → fix → confirm the reproduction no longer fails.
- Never claim a test or check passed that you did not actually run.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Verify invalid inputs are rejected"
- "Fix the bug" → "Reproduce it, fix it, then verify the reproduction no longer fails"
- "Refactor X" → "Verify behavior is unchanged before and after"

For multi-step tasks, state a brief plan:
```md
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```
Strong success criteria let you loop independently. Weak criteria (“make it work”) require constant clarification.


These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---
# Agent skills

## Issue tracker

Issues live as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

## Triage labels

Default five-state vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

## Domain docs

Single-context repo — primary source of truth is `documents/PRD.md`; `CONTEXT.md` and `docs/adr/` are created lazily via `/grill-with-docs`. See `docs/agents/domain.md`.

## grill-with-docs

When a task involves unclear domain language, feature boundaries, data meaning, workflow assumptions, or non-obvious product decisions, ask whether we should run `/grill-with-docs` before implementation.

Use `/grill-with-docs` to clarify terminology, propse updates `CONTEXT.md`, and propose ADRs for important non-obvious decisions.
When the mode is auto, change to accept mode.  

Do not automatically run it for every task. For small, obvious, or purely mechanical changes, proceed normally.