---
name: knowledge-evolution
description: Three-step structured knowledge base evolution: scan → connection discovery → action list. v1.0.0: 4-dimension quality scoring, 3-layer connection discovery (TF-IDF + relation classification + cross-domain bridging), 3 action execution paths (supplement/create/connect), create-only with references.
---

Knowledge base self-evolution aApp. Runs three-step structured evolution (scan → connection discovery → action list), plus topic deep-dive and history view, on the user's Remio knowledge base.

Runtime: `embedded`.

## Endpoints

### `GET /` [UI]
Show the hub with 4 evolution options (weekly, monthly, deep dive, history). No params. This is the activation target.

### `GET /scan_ui` [UI]
Run a knowledge base scan with 4-dimension quality scoring and diversity selection. Optional params:
- `range_type` (string, optional: `week` | `month` | `custom`, default `week`)
- `start_date` (string, YYYY-MM-DD, required when `range_type=custom`)
- `end_date` (string, YYYY-MM-DD, required when `range_type=custom`)
- `save` (string, `true` | `false`, default `true`) — save report as a note + add to collection

Flow: compute date range → `search_notes` (time filter, sort by created, limit 200) → filter noise → group by type → extract keywords → Round 2 keyword search → **4-dimension quality scoring** (content density, uniqueness, structure, freshness) → **pick TOP 10 with diversity** (per-keyword cap 2 + MMR reranking, λ=0.7) → LLM semantic labels → save report note → return UI.

### `GET /connections_ui` [UI]
3-layer connection discovery: (1) TF-IDF cosine similarity filter → (2) LLM relation classification (support/extend/contradict/complement/transfer) → (3) cross-domain bridging. Optional params:
- `report_id` (string, optional) — scan report note id. If empty, falls back to the latest scan from local state.

### `GET /actions_ui` [UI]
Generate 3-7 actionable suggestions with 3 types: supplement (补充已有笔记), create (创造新笔记), connect (建立关联). Optional params:
- `report_id` (string, optional) — scan report note id. If empty, falls back to the latest from local state.

Returns UI with priority-tagged cards (P0/P1/P2), estimated time, action type label, and an "✅ 执行" button on each card.

### `POST /actions_ui` [UI]
Execute an action. Two-step flow: (1) `execute=true` shows execution form, (2) `execute=true + confirm=true` creates the note. Params:
- `report_id` (string, required)
- `execute` (string) — if `true`, shows execution form
- `confirm` (string) — if `true`, confirms and creates the note
- `action_index` (string) — 0-based index of the action to execute

**Core principle**: Never modifies original notes. Always creates new notes + references originals.
- supplement → new note with补充内容 + reference to original
- create → new structured note based on scan findings
- connect → new note explaining the connection + references to both items

### `GET /deep_dive_ui` [UI]
Show the deep dive input form (topic + time range). Submitting the form posts to `POST /deep_dive_ui` with `{topic, time_range}`. No params.

### `POST /deep_dive_ui` [UI]
Run a deep dive on a topic. With empty topic, returns the input form. With a topic, searches the knowledge base, then LLM writes a structured report (core points, hidden connections, action suggestions). Params:
- `topic` (string, required for dive)
- `input` (string, from shortcut `<<evo <text>`) — fallback when topic is empty
- `time_range` (string, optional: `week` | `month` | `all`, default `all`)
- `save` (string, `true` | `false`, default `true`)

Shortcut: `<<evo <topic>` → `POST /deep_dive_ui` with `{input: <topic>}`.

### `GET /history_ui` [UI]
List historical evolution reports from the "知识库进化" collection, filtered by type. Params:
- `type` (string, optional: `all` | `weekly` | `monthly` | `deep_dive`, default `all`)
- `limit` (string, optional, default `20`)

## User interactions

- Open 知识库自我进化 → `GET /` (hub)
- Run weekly evolution → `GET /scan_ui?range_type=week`
- Run monthly health check → `GET /scan_ui?range_type=month`
- Custom range scan → `GET /scan_ui?range_type=custom` (returns form first)
- Discover connections → `GET /connections_ui` (uses latest scan)
- Generate action list → `GET /actions_ui` (uses latest)
- Execute an action → click "✅ 执行" on action card → `POST /actions_ui` with execute/confirm
- Deep dive on a topic → `POST /deep_dive_ui` with `{topic: "..."}` or use `<<evo <topic>>` shortcut
- View history → `GET /history_ui`

## Persistence

- Each report (scan / connections / actions / deep-dive) is saved as a Remio note and added to the collection `知识库进化`.
- Action execution notes (supplement/connection/creation) are also saved to the collection.
- Local `state.json` caches the latest report id per type for fast `connections_ui` / `actions_ui` lookups (the source of truth is the collection).

## Constraints

- Time range capped at 90 days (per PRD §4)
- Max 10 connection pairs
- Action list size: 3-7 items
- search_notes returns 0 results → UI shows "无新增"
- LLM prompts: failures are caught and surfaced as a friendly UI error; no exception escapes
- All endpoints return the `components` shape; no bare strings
- **Never modifies original notes** — always creates new + references

## Persistence: report title conventions

- `📊 知识库扫描 - {date} ({label})` — scan
- `🧠 知识关联发现 - {date}` — connections
- `✅ 行动清单 - {date}` — actions
- `🔍 专题深挖 - {topic} - {date}` — deep dive
- `📝 补充：{action}` — supplement execution
- `🔗 关联：{action}` — connection execution
- `✨ {action}` — creation execution
