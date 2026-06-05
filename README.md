# 🧬 Knowledge Evolution (知识库自我进化)

> 三步结构化知识库进化：扫描 → 关联发现 → 行动清单。

A Remio embedded aApp that runs structured evolution on your personal knowledge base — discover what you know, find hidden connections, and generate actionable next steps.

---

## ✨ Features

- **📊 Multi-dimensional scan** — weekly / monthly / custom range, with 4-dimension quality scoring (content density, uniqueness, structure, freshness)
- **🧠 3-layer connection discovery** — TF-IDF cosine filter → LLM relation classification → cross-domain bridging. Heuristic fallback when LLM returns empty.
- **✅ Action list** — 3-7 priority-tagged actions (P0/P1/P2) with 3 execution paths: supplement / create / connect. **Never modifies original notes** — always creates new + references.
- **🔍 Topic deep dive** — single-topic structured report with core points, hidden connections, and action suggestions.
- **📚 History view** — browse all past evolution reports.
- **📑 Report dedup** — automatically deletes same-date + same-type duplicates.
- **🔗 Built-in LLM fallback** — connections and actions handlers both have graceful degradation when LLM fails.

## 📦 Project Structure

```
knowledge-evolution/
├── manifest.json     # aapp manifest (id, name, version, chat menu, shortcuts)
├── api.json          # endpoint contracts (paths, params, types)
├── logic.py          # all handlers + LLM prompts (single file)
├── SKILL.md          # aapp self-description
├── icon.svg          # emoji placeholder
└── README.md         # this file
```

## 🚀 Quick Start

### Install (Remio)

```bash
# 1. Place this directory at:
#    <remio-user-dir>/remio/aapps-dev/knowledge-evolution/knowledge-evolution/
# 2. Open Remio → aapp-studio → validate
# 3. Deploy from aapp-studio
```

### Use in chat

```
🚀 一键进化 → 周度进化      # run weekly scan
🧠 知识关联发现              # discover connections (uses latest scan)
✅ 生成行动清单              # generate action list
🚀 一键进化 → 月度体检      # run monthly health check
🔍 深挖 → 输入主题            # deep dive on a topic
📚 历史                     # browse history
```

Shortcut: `<<evo <topic>>` runs a deep dive.

## 🛠 Architecture

### Scan flow

```
search_notes (time filter, sort by created)
  → filter noise (title < 3 + preview < 10 chars)
  → group by note type
  → extract keywords (frequency-based)
  → round 2 keyword search (enrich)
  → 4-dimension quality scoring
  → pick TOP 10 (per-keyword cap 2 + MMR reranking, λ=0.7)
  → LLM semantic labels (label + value)
  → save report note + cache top_items to state.json
```

### Connection flow

```
top_items (from scan state)
  → Layer 1: TF-IDF cosine similarity (title + preview)
  → Layer 2: LLM relation classification (支撑/延伸/矛盾/互补/迁移/duplicate)
  → Layer 2 fallback: if LLM returns empty, use top TF-IDF pairs as heuristic connections
  → Layer 3: LLM cross-domain bridging
  → dedup by pair_id → enrich with source/target titles
  → save connections report
```

### Action flow

```
top_items (from scan state)
  → LLM action generation (3-7 items, P0/P1/P2)
  → Fallback: if LLM returns empty, auto-generate P0/P1/P2 from top3 items
  → save action list report
  → on execute: never modify original, always create new + reference
```

## 📝 Persistence

- Each report saved as Remio note, added to `知识库进化` collection.
- Action execution notes (supplement / connection / creation) also saved to collection.
- Local `state.json` caches latest report id per type for fast lookups.

Report title conventions:
- `📊 知识库扫描 - {date} ({label})` — scan
- `🧠 知识关联发现 - {date}` — connections
- `✅ 行动清单 - {date}` — actions
- `🔍 专题深挖 - {topic} - {date}` — deep dive
- `📝 补充：{action}` — supplement execution
- `🔗 关联：{action}` — connection execution
- `✨ {action}` — creation execution

## 🔒 Constraints

- Time range capped at 90 days
- Max 10 connection pairs
- Action list size: 3-7 items
- search_notes returns 0 results → UI shows "无新增"
- LLM failures are caught and surfaced as friendly UI error; no exception escapes
- All endpoints return `components` shape; no bare strings
- **Never modifies original notes** — always creates new + references

## 📄 License

MIT

## 👤 Author

violin
