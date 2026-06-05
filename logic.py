"""Knowledge Evolution aApp v1.0.0.

Three-step structured knowledge base evolution flow:
  1. Scan — full library scan with quality scoring + diversity
  2. Connections — 3-layer hybrid discovery (semantic filter + relation classify + cross-domain)
  3. Actions — 3 action types (supplement/create/connect) with execution paths

Plus: topic deep dive + history view.

v1.0.0 changes:
  - Scan: 4-dimension quality scoring (content density, uniqueness, structure, freshness)
  - Connections: 3-layer discovery (TF-IDF cosine filter → relation type classification → cross-domain bridging)
  - Actions: 3 types with distinct execution paths (supplement → new note + reference; create → structured draft; connect → relation note)
  - Knowledge marking (core/active/isolated) based on connection graph
  - Action execution: never modifies original notes, always creates new + references
"""

import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from remio_sdk import (
    add_note_to_collection,
    create_aapp_logger,
    create_note,
    delete_note,
    get_state,
    read_note,
    router,
    run_prompt,
    search_notes,
    set_state,
)

AAPP_ID = "knowledge-evolution"
COLLECTION_TITLE = "知识库进化"

MAX_CONNECTIONS = 10
MIN_ACTIONS = 3
MAX_ACTIONS = 7
MAX_RANGE_DAYS = 90

LOG_DIR = os.path.join("/tmp", "knowledge-evolution", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Type weights: higher = more likely to be "real knowledge"
# ---------------------------------------------------------------------------
TYPE_WEIGHTS = {
    "note": 10,
    "highlight_note": 9,
    "resource": 8,
    "file": 7,
    "recording": 6,
    "podcast": 6,
    "mail": 4,
    "slack": 3,
    "ai_chat": 0,
}

EXCLUDED_TYPES = {"ai_chat"}

_AI_LOG_PATTERNS = [
    r"^今天的定时任务", r"^让我", r"^我来", r"^好的，", r"^开始执行",
    r"^执行摘要", r"^以下是", r"^全部完成", r"^总结一下", r"^好的！",
    r"^搞定了", r"^来看下", r"^确认：", r"^现在进入", r"^Phase \d",
]


class _FallbackLog:
    def info(self, *_a, **_k): pass
    def warn(self, *_a, **_k): pass
    def error(self, *_a, **_k): pass


try:
    log = create_aapp_logger(AAPP_ID, LOG_DIR, "knowledge-evolution-logic")
except Exception:
    log = _FallbackLog()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _date_range_for(range_type, start_date="", end_date=""):
    today = datetime.now().date()
    if range_type == "month":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if range_type == "custom" and start_date and end_date:
        try:
            s = datetime.fromisoformat(start_date).date()
            e = datetime.fromisoformat(end_date).date()
        except ValueError:
            s = today - timedelta(days=7)
            e = today
        if (e - s).days > MAX_RANGE_DAYS:
            s = e - timedelta(days=MAX_RANGE_DAYS)
        return s.isoformat(), e.isoformat()
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


def _range_label(range_type, start, end):
    if range_type == "week":
        return "本周"
    if range_type == "month":
        return "近 30 天"
    return f"{start} → {end}"


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

def _is_ai_log_title(title):
    t = (title or "").strip()
    if not t:
        return True
    for pat in _AI_LOG_PATTERNS:
        if re.match(pat, t):
            return True
    return False


def _filter_noise(items):
    filtered = []
    for it in items:
        ttype = it.get("type") or "note"
        if ttype in EXCLUDED_TYPES:
            continue
        title = (it.get("title") or "").strip()
        if _is_ai_log_title(title):
            continue
        preview = (it.get("preview") or "").strip()
        if len(title) < 3 and len(preview) < 10:
            continue
        it = dict(it)
        it["_type_weight"] = TYPE_WEIGHTS.get(ttype, 5)
        filtered.append(it)
    return filtered


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def _search_range(start, end, limit=200):
    try:
        result = search_notes({
            "time_filter": {"start": start, "end": end},
            "sort_by": "created",
            "limit": limit,
        })
        if isinstance(result, dict) and result.get("ok"):
            return result.get("data", {}).get("results", []) or []
    except Exception as e:
        log.error("search_range failed", {"error": str(e), "start": start, "end": end})
    return []


def _search_range_by_relevance(start, end, query, limit=100):
    try:
        result = search_notes({
            "query": query,
            "time_filter": {"start": start, "end": end},
            "sort_by": "relevance",
            "limit": limit,
        })
        if isinstance(result, dict) and result.get("ok"):
            return result.get("data", {}).get("results", []) or []
    except Exception as e:
        log.error("search_range_by_relevance failed", {"error": str(e), "query": query})
    return []


def _search_topic(topic, time_range, limit=30):
    params = {"query": topic, "sort_by": "created", "limit": limit}
    if time_range in ("week", "month"):
        start, end = _date_range_for(time_range)
        params["time_filter"] = {"start": start, "end": end}
    try:
        result = search_notes(params)
        if isinstance(result, dict) and result.get("ok"):
            return result.get("data", {}).get("results", []) or []
    except Exception as e:
        log.error("search_topic failed", {"error": str(e), "topic": topic})
    return []


def _group_by_type(items):
    by_type = {}
    for it in items:
        t = it.get("type") or "note"
        by_type[t] = by_type.get(t, 0) + 1
    return by_type


_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "on", "at", "by",
    "with", "from", "as", "be", "this", "that", "it", "its", "are", "was", "were",
    "的", "了", "是", "在", "和", "与", "或", "一个", "一些", "什么", "怎么", "如何",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "这", "那", "这个", "那个",
    "有", "没", "也", "就", "都", "要", "会", "可以", "能",
}


def _extract_keywords(items, top_n=10):
    counter = Counter()
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        tokens = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", title)
        for tok in tokens:
            tok_l = tok.lower()
            if tok_l in _STOPWORDS or len(tok_l) < 2:
                continue
            counter[tok_l] += 1
    return [w for w, _ in counter.most_common(top_n)]


# ---------------------------------------------------------------------------
# Quality scoring (v1.0.0 — 4 dimensions)
# ---------------------------------------------------------------------------

def _tokenize(text):
    """Tokenize text into words (Chinese chars as individual tokens, English as words)."""
    return re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", text or "")


def _content_density_score(item):
    """Score 0-10: ratio of content length to title length. Longer content = higher density."""
    title_len = len(item.get("title") or "")
    preview_len = len(item.get("preview") or "")
    if title_len == 0:
        return 0
    ratio = preview_len / title_len
    return min(10, int(ratio * 2))


def _uniqueness_score(item):
    """Score 0-10: ratio of unique words to total words in preview. Higher = more information-dense."""
    tokens = _tokenize((item.get("preview") or "").lower())
    if not tokens:
        return 0
    unique_ratio = len(set(tokens)) / len(tokens)
    return min(10, int(unique_ratio * 10))


def _structure_score(item):
    """Score 0-10: presence of structured content markers in preview."""
    preview = item.get("preview") or ""
    score = 0
    if "#" in preview:
        score += 2  # has headings
    if "-" in preview or "•" in preview or "*" in preview:
        score += 2  # has lists
    if "```" in preview or "`" in preview:
        score += 2  # has code
    if "http" in preview:
        score += 1  # has links
    if re.search(r"\d+[.、]", preview):
        score += 2  # has numbered items
    if "---" in preview or "===" in preview:
        score += 1  # has separators
    return min(10, score)


def _freshness_score(item):
    """Score 0-10: based on creation date. More recent = higher score."""
    created = item.get("createdAt") or item.get("created") or 0
    if not created:
        return 5
    try:
        if isinstance(created, str):
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        elif isinstance(created, (int, float)):
            dt = datetime.fromtimestamp(created / 1000 if created > 1e12 else created)
        else:
            return 5
        days_ago = (datetime.now() - dt).days
        if days_ago <= 3:
            return 10
        if days_ago <= 7:
            return 8
        if days_ago <= 30:
            return 6
        if days_ago <= 90:
            return 4
        return 2
    except Exception:
        return 5


def _compute_quality_score(item):
    """Compute overall quality score (0-100) from 4 dimensions."""
    density = _content_density_score(item)
    uniqueness = _uniqueness_score(item)
    structure = _structure_score(item)
    freshness = _freshness_score(item)
    type_w = item.get("_type_weight", 5)

    # Weighted sum: density 25%, uniqueness 25%, structure 15%, freshness 20%, type 15%
    score = (
        density * 2.5
        + uniqueness * 2.5
        + structure * 1.5
        + freshness * 2.0
        + type_w * 1.5
    )
    return round(min(100, score), 1)


# ---------------------------------------------------------------------------
# Diversity selection (v0.2.0 — kept)
# ---------------------------------------------------------------------------

def _jaccard_sim(title_a, title_b):
    def _tokens(t):
        return set(tok.lower() for tok in re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", t) if len(tok) >= 2)
    ta, tb = _tokens(title_a), _tokens(title_b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _pick_top_items(items, n=10, keywords=None, per_keyword_cap=2, mmr_lambda=0.7):
    if not items:
        return []

    kw_set = set()
    if keywords:
        for w in keywords:
            wl = (w or "").strip().lower()
            if wl and len(wl) >= 2:
                kw_set.add(wl)

    def _kw_hits(it):
        haystack = ((it.get("title") or "") + " " + (it.get("preview") or "")).lower()
        return sum(1 for kw in kw_set if kw and kw in haystack)

    def _primary_keyword(it):
        haystack = ((it.get("title") or "") + " " + (it.get("preview") or "")).lower()
        best_kw, best_len = None, 0
        for kw in kw_set:
            if kw and kw in haystack and len(kw) > best_len:
                best_kw = kw
                best_len = len(kw)
        return best_kw or "_other"

    scored = sorted(
        items,
        key=lambda it: (_kw_hits(it) * 10 + it.get("_type_weight", 5), len(it.get("title") or "")),
        reverse=True,
    )

    kw_count = Counter()
    capped = []
    for it in scored:
        pk = _primary_keyword(it)
        if kw_count[pk] < per_keyword_cap:
            capped.append(it)
            kw_count[pk] += 1
        if len(capped) >= n * 3:
            break

    if not capped:
        return scored[:n]

    def _relevance(it):
        return _kw_hits(it) * 100 + it.get("_type_weight", 5) * 5 + min(len(it.get("title") or "") // 20, 5)

    selected = [capped[0]]
    candidates = capped[1:]

    while len(selected) < n and candidates:
        best_idx = -1
        best_mmr = -float("inf")
        for idx, cand in enumerate(candidates):
            rel = _relevance(cand)
            max_sim = max(_jaccard_sim(cand.get("title", ""), s.get("title", "")) for s in selected)
            mmr_score = mmr_lambda * rel - (1 - mmr_lambda) * max_sim * 100
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = idx
        if best_idx >= 0:
            selected.append(candidates.pop(best_idx))
        else:
            break

    if not kw_set and len(selected) < n:
        remaining = [it for it in scored if it not in selected]
        selected.extend(remaining[: n - len(selected)])

    return selected[:n]


# ---------------------------------------------------------------------------
# TF-IDF cosine similarity (v1.0.0 — for connection candidate filtering)
# ---------------------------------------------------------------------------

def _build_tfidf_vectors(items):
    """Build TF-IDF vectors for items based on title + preview. Returns (vocabulary, vectors)."""
    # Build document tokens
    doc_tokens = []
    for it in items:
        text = (it.get("title", "") + " " + (it.get("preview") or "")).lower()
        tokens = [t for t in _tokenize(text) if t not in _STOPWORDS and len(t) >= 2]
        doc_tokens.append(tokens)

    # Build vocabulary
    vocab = {}
    for tokens in doc_tokens:
        for t in tokens:
            if t not in vocab:
                vocab[t] = len(vocab)

    # Compute IDF
    n_docs = len(doc_tokens)
    idf = {}
    for term in vocab:
        df = sum(1 for tokens in doc_tokens if term in tokens)
        idf[term] = math.log((n_docs + 1) / (df + 1)) + 1

    # Compute TF-IDF vectors
    vectors = []
    for tokens in doc_tokens:
        tf = Counter(tokens)
        vec = {}
        for term, count in tf.items():
            if term in vocab:
                vec[term] = (count / max(len(tokens), 1)) * idf.get(term, 1)
        vectors.append(vec)

    return vocab, vectors


def _cosine_similarity(vec_a, vec_b):
    """Cosine similarity between two sparse vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a.keys()) & set(vec_b.keys())
    if not common:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _parse_json_array(text):
    text = (text or "").strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else []
    except Exception:
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return []


def _parse_json_object(text):
    text = (text or "").strip()
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, dict) else None
        except Exception:
            pass
    return None


def _llm_label_items(top_items):
    """Use LLM to generate short semantic labels + value statements for TOP items."""
    items_for_llm = []
    for i, it in enumerate(top_items):
        items_for_llm.append({
            "idx": i,
            "title": (it.get("title") or "")[:100],
            "type": it.get("type", "note"),
            "preview": (it.get("preview") or "")[:200],
        })

    prompt = (
        "你是一个知识库分析助手。为下面每个知识条目生成：\n"
        "1. label: 3-6 字的简短标签（如「DNS 修复」「BizyAir 创作」「提示词挖掘」）\n"
        "2. value: 一句话（20 字以内）说明「这条内容为什么值得关注」\n\n"
        "规则：\n"
        "- 标签要具体、可辨识，不要用泛泛的「AI」「笔记」「内容」\n"
        "- 价值说明要从内容出发，指出具体的知识增量或行动启发\n"
        "- 如果内容是 AI 工作日志/执行过程（如「今天的定时任务显示……」），标签标为「工作日志」，价值说明标为「可忽略」\n\n"
        f"条目列表：\n{json.dumps(items_for_llm, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON 数组（无 markdown 代码块、无额外文本），格式：\n"
        '[{"idx": 0, "label": "标签", "value": "价值说明"}, ...]\n'
        "数组长度必须与输入条目数相同。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=30000)
        if isinstance(result, dict) and result.get("ok"):
            text = result.get("output", "")
            labels = _parse_json_array(text)
            if not labels:
                log.warn("llm_label_items parse_fail", {"raw": text[:500]})
            label_map = {}
            for lb in labels:
                idx = lb.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(top_items):
                    label_map[idx] = lb
            for i in range(len(top_items)):
                if i not in label_map:
                    label_map[i] = {"idx": i, "label": top_items[i].get("type", "note"), "value": "知识条目"}
            return [label_map[i] for i in range(len(top_items))]
        log.warn("llm_label_items no ok", {"result": str(result)[:200]})
    except Exception as e:
        log.error("llm_label_items failed", {"error": str(e)})
    return [{"idx": i, "label": it.get("type", "note"), "value": "知识条目"}
            for i, it in enumerate(top_items)]


def _llm_classify_relations(pairs, top_items):
    """Use LLM to classify relation types and explain.

    v1.0.0: Pre-classify relation types (support/extend/contradict/complement/transfer)
    instead of free-form reasoning.
    """
    if not pairs:
        return []

    prompt = (
        "你是一个跨领域知识关联分析专家。对每组条目对，完成两步分析：\n\n"
        "第一步：选择关联类型（只选一个）：\n"
        "- 支撑：A 的论点/方法支撑了 B\n"
        "- 延伸：B 是 A 的深化或延伸\n"
        "- 矛盾：A 和 B 的观点冲突\n"
        "- 互补：A 和 B 从不同角度看同一问题\n"
        "- 迁移：A 的方法可以应用到 B 的领域\n\n"
        "第二步：用一句话（30 字以内）说明【为什么 A 的知识能帮助理解或应用 B】。\n\n"
        "规则：\n"
        "- 不要指出表面上的关键词重复（如「都是 AI」）\n"
        "- 如果两个条目本质上说的是同一件事，relation_type 设为 \"duplicate\"\n"
        "- 宁可少给几组高质量的关联，也不要凑数\n\n"
        f"条目对：\n{json.dumps(pairs, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON 数组（无 markdown 代码块、无额外文本），格式：\n"
        '[{"pair_id": "p0_1", "relation_type": "支撑|延伸|矛盾|互补|迁移|duplicate", "reason": "一句话中文原因"}]\n\n'
        f"最多 {MAX_CONNECTIONS} 对。只返回真正有洞见的关联。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            text = result.get("output", "")
            ranked = _parse_json_array(text)
            if not ranked:
                log.warn("llm_classify_relations parse_fail", {"raw": text[:500]})
            return ranked[:MAX_CONNECTIONS]
        log.warn("llm_classify_relations no ok", {"result": str(result)[:200]})
    except Exception as e:
        log.error("llm_classify_relations failed", {"error": str(e)})
    return []


def _llm_find_cross_domain(top_items, connections):
    """v1.0.0 Layer 3: Find cross-domain bridging connections.

    Specifically looks for connections between items of different types
    or items in different topic clusters.
    """
    # Group items by type
    type_groups = {}
    for i, it in enumerate(top_items):
        t = it.get("type", "note")
        if t not in type_groups:
            type_groups[t] = []
        type_groups[t].append(i)

    # Find pairs across different types that aren't already in connections
    existing_pairs = set()
    for conn in connections:
        pid = conn.get("pair_id", "")
        if pid:
            existing_pairs.add(pid)

    cross_pairs = []
    types = list(type_groups.keys())
    for ti in range(len(types)):
        for tj in range(ti + 1, len(types)):
            for ai in type_groups[types[ti]]:
                for bi in type_groups[types[tj]]:
                    pid = f"p{ai}_{bi}"
                    if pid not in existing_pairs:
                        cross_pairs.append({
                            "pair_id": pid,
                            "item_a": {
                                "title": top_items[ai].get("title", ""),
                                "label": top_items[ai].get("label", ""),
                                "type": top_items[ai].get("type", "note"),
                                "value": top_items[ai].get("value", ""),
                                "preview": (top_items[ai].get("preview") or "")[:150],
                            },
                            "item_b": {
                                "title": top_items[bi].get("title", ""),
                                "label": top_items[bi].get("label", ""),
                                "type": top_items[bi].get("type", "note"),
                                "value": top_items[bi].get("value", ""),
                                "preview": (top_items[bi].get("preview") or "")[:150],
                            },
                        })

    if not cross_pairs:
        return []

    # Limit to top 10 candidates
    cross_pairs = cross_pairs[:10]

    prompt = (
        "你是一个跨领域知识整合专家。下面每组条目对来自不同的内容类型。\n"
        "你的任务是发现它们之间的【跨域桥梁】——看似无关，但底层有深层联系。\n\n"
        "规则：\n"
        "- 寻找方法论迁移、底层原理相通、认知框架互补这类深层联系\n"
        "- 如果确实没有有价值的跨域联系，跳过该对\n"
        "- 只返回最有洞见的 1-3 组跨域关联\n\n"
        f"跨型条目对：\n{json.dumps(cross_pairs, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON 数组（无 markdown 代码块、无额外文本），格式：\n"
        '[{"pair_id": "p0_3", "relation_type": "迁移|互补", "reason": "一句话中文原因"}]\n'
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            text = result.get("output", "")
            bridges = _parse_json_array(text)
            return bridges[:3]
    except Exception as e:
        log.error("llm_find_cross_domain failed", {"error": str(e)})
    return []


def _llm_generate_actions(scan_summary, connections):
    """Use LLM to generate actionable suggestions with type classification.

    v1.0.1: Pass richer context (titles of top items + connection reasons).
    """
    # Build human-readable context for LLM
    items_lines = []
    for i, t in enumerate(scan_summary.get("top_items", [])[:10], 1):
        items_lines.append(f"{i}. {t.get('title', '')} ({t.get('type', '')})")
    conn_lines = []
    for c in connections[:5]:
        rtype = c.get("relation_type", "")
        reason = c.get("reason", "")
        source = c.get("source_title", "?")
        target = c.get("target_title", "?")
        conn_lines.append(f"- [{rtype}] {source} ↔ {target}: {reason}")

    items_text = "\n".join(items_lines)
    conn_text = "\n".join(conn_lines) if conn_lines else "暂无关联"

    prompt = (
        f"你是一个个人知识行动教练。根据下面的知识进化报告，生 {MIN_ACTIONS}-{MAX_ACTIONS} 条具体可执行的行动建议。\n\n"
        f"知识库 TOP 条目：\n{items_text}\n\n"
        f"发现的关联：\n{conn_text}\n\n"
        "每条行动必须包含 action_type，决定执行路径：\n"
        "- supplement：需要补充某条已有笔记的内容（related_note_id 必填）\n"
        "- create：基于发现创造一篇全新的笔记\n"
        "- connect：在两条笔记之间建立关联（related_note_ids 填两条）\n\n"
        "只输出 JSON 数组（无 markdown 代码块、无额外文本），格式：\n"
        '[{"priority": "P0|P1|P2", "action_type": "supplement|create|connect", "action": "具体行动（中文）", "estimated_time": "5min|30min|2h", "related_note_id": "笔记ID或空", "related_note_ids": ["id1", "id2"], "related_label": "关联的标签（可选）"}]\n\n'
        "按优先级排序（P0 优先）。P0 = 今天做，P1 = 本周做，P2 = 下周做。\n\n"
        "关键要求：\n"
        "1. 每条行动要具体到能立刻开始做，不要泛泛而谈\n"
        "2. 不要建议「写 README」或「重命名文件」这种低价值行动\n"
        "3. 重点关注：知识结构化、跨领域关联、从发现到实践的转化\n"
        "4. supplement 类：related_note_id 填目标笔记标题（不是 ID），action 描述要补充什么具体内容\n"
        "5. connect 类：related_note_ids 填两条笔记标题，action 描述关联逻辑\n"
        "6. create 类：action 描述要创造什么新知识，格式要具体（如「创建一份 XX 流程模板」而非「写一篇笔记」）"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            text = result.get("output", "")
            actions = _parse_json_array(text)
            if not actions:
                log.warn("llm_generate_actions parse_fail", {"raw": text[:500]})
            else:
                return actions[:MAX_ACTIONS]
        else:
            log.warn("llm_generate_actions no_ok", {"ok": result.get("ok"), "error": str(result.get("error", ""))[:200]})
    except Exception as e:
        log.error("llm_generate_actions failed", {"error": str(e)})

    # Fallback: generate simple actions from top_items when LLM fails
    fallback = []
    if scan_summary.get("top_items"):
        top3 = scan_summary["top_items"][:3]
        fallback.append({
            "priority": "P0", "action_type": "supplement",
            "action": f"补充笔记「{top3[0]['title']}」的最新内容和发展",
            "estimated_time": "30min",
            "related_note_id": top3[0].get("noteId") or top3[0].get("title", ""),
        })
        if len(top3) > 1:
            fallback.append({
                "priority": "P1", "action_type": "connect",
                "action": f"分析「{top3[0]['title']}」和「{top3[1]['title']}」之间的关联",
                "estimated_time": "20min",
                "related_note_ids": [
                    top3[0].get("noteId") or top3[0].get("title", ""),
                    top3[1].get("noteId") or top3[1].get("title", ""),
                ],
            })
        if len(top3) > 2:
            fallback.append({
                "priority": "P2", "action_type": "create",
                "action": f"基于「{top3[2]['title']}」创造一篇知识整合笔记",
                "estimated_time": "1h",
                "related_note_id": top3[2].get("noteId", ""),
            })
    return fallback


def _llm_deep_dive(topic, related_notes):
    """Use LLM to write a deep dive report."""
    notes_summaries = []
    for n in related_notes[:10]:
        notes_summaries.append({
            "title": (n.get("title", "") or "")[:100],
            "type": n.get("type", "note"),
            "preview": (n.get("preview") or "")[:200],
        })
    prompt = (
        f"你是一个知识库深度研究助手。围绕主题「{topic}」写一份深度分析报告（中文）。\n\n"
        f"用户知识库中相关笔记：\n"
        f"{json.dumps(notes_summaries, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON 对象（无 markdown 代码块、无额外文本），格式：\n"
        '{"title": "报告标题（中文）", "core_points": ["...", "..."], "hidden_connections": ["...", "..."], "action_suggestions": ["...", "..."]}\n\n'
        "core_points：3-5 个核心要点。\n"
        "hidden_connections：2-4 个隐藏关联。\n"
        "action_suggestions：2-3 个具体行动建议。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            text = result.get("output", "")
            parsed = _parse_json_object(text)
            if parsed is None:
                log.warn("llm_deep_dive parse_fail", {"topic": topic, "raw": text[:500]})
            return parsed
        log.warn("llm_deep_dive no ok", {"topic": topic, "result": str(result)[:200]})
    except Exception as e:
        log.error("llm_deep_dive failed", {"error": str(e), "topic": topic})
    return None


def _llm_draft_supplement(topic, original_content):
    """v1.0.0: Generate a supplement draft for an existing note."""
    prompt = (
        f"你是一个知识补充助手。你需要为下面这篇笔记生成补充内容。\n\n"
        f"原笔记主题：{topic}\n"
        f"原笔记内容：\n{original_content[:2000]}\n\n"
        "生成补充内容（中文），要求：\n"
        "1. 补充原笔记中缺失的信息、细节或最新进展\n"
        "2. 保持与原笔记风格一致\n"
        "3. 用 markdown 格式输出\n"
        "4. 不要重复原笔记已有的内容\n"
        "5. 如果原笔记已经很完善，指出「暂无补充建议」\n\n"
        "只输出补充内容本身（无 JSON 包装、无额外说明）。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            return result.get("output", "").strip()
    except Exception as e:
        log.error("llm_draft_supplement failed", {"error": str(e)})
    return ""


def _llm_draft_creation(topic, scan_context, related_contents=None):
    """v1.0.3: Generate a structured draft for a new knowledge note.
    
    related_contents: list of {title, content} dicts from actual notes.
    """
    content_section = ""
    if related_contents:
        parts = []
        for i, rc in enumerate(related_contents[:3], 1):
            parts.append(f"### 参考笔记 {i}: {rc.get('title', '无标题')}\n{rc.get('content', '')[:2000]}")
        content_section = "\n\n## 以下是知识库中相关笔记的实际内容：\n\n" + "\n\n".join(parts)

    prompt = (
        f"你是一个知识创作助手。基于下面的知识库材料，为「{topic}」创建一篇结构化笔记草稿。\n\n"
        f"扫描背景：\n{scan_context}\n"
        f"{content_section}\n\n"
        "生成笔记草稿（中文），要求：\n"
        "1. 结构清晰：标题、背景、核心要点、实践建议、参考链接\n"
        "2. 内容要有深度，基于上面的参考笔记内容展开，不要泛泛概述\n"
        "3. 用 markdown 格式输出\n"
        "4. 标注哪些是基于参考笔记的发现，哪些是需要进一步验证的\n"
        "5. 如果参考笔记中有具体案例、数据或方法，务必引用\n\n"
        "只输出笔记内容本身（无 JSON 包装、无额外说明）。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        log.info("llm_draft_creation.result", {
            "ok": result.get("ok") if isinstance(result, dict) else "not_dict",
            "output_len": len(result.get("output", "")) if isinstance(result, dict) else 0,
            "error": result.get("error", "") if isinstance(result, dict) else str(result),
        })
        if isinstance(result, dict) and result.get("ok"):
            output = result.get("output", "").strip()
            if output:
                return output
            log.warning("llm_draft_creation.empty_output")
        else:
            log.warning("llm_draft_creation.not_ok", {"result_keys": list(result.keys()) if isinstance(result, dict) else type(result).__name__})
    except Exception as e:
        log.error("llm_draft_creation failed", {"error": str(e)})
    return ""


def _llm_draft_connection(item_a_title, item_a_preview, item_b_title, item_b_preview):
    """v1.0.0: Generate a connection note explaining why two items are related."""
    prompt = (
        f"你是一个知识关联分析助手。写一段说明，解释为什么下面两个知识条目有关联。\n\n"
        f"条目 A：{item_a_title}\n"
        f"内容预览：{(item_a_preview or '')[:200]}\n\n"
        f"条目 B：{item_b_title}\n"
        f"内容预览：{(item_b_preview or '')[:200]}\n\n"
        "写一段 100-200 字的说明（中文），要求：\n"
        "1. 解释两个条目之间的深层联系\n"
        "2. 说明理解其中一个如何帮助理解另一个\n"
        "3. 用 markdown 格式输出\n"
        "4. 不要泛泛而谈，要有具体洞见\n\n"
        "只输出说明内容本身（无 JSON 包装、无额外说明）。"
    )
    try:
        result = run_prompt(prompt=prompt, timeout_ms=60000)
        if isinstance(result, dict) and result.get("ok"):
            return result.get("output", "").strip()
    except Exception as e:
        log.error("llm_draft_connection failed", {"error": str(e)})
    return ""


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_report(title, content, report_type, date_str):
    try:
        # v1.0.2: dedup — delete existing reports with same date+type to avoid duplicates
        try:
            existing = search_notes({"query": f"📊 知识库扫描 - {date_str}" if report_type in ("weekly", "monthly", "custom") else title.split(" - ")[0] if " - " in title else title, "limit": 10})
            for ex in (existing.get("data", {}).get("results", []) if isinstance(existing, dict) else []):
                ex_id = ex.get("noteId")
                ex_title = ex.get("title", "")
                # Match same report_type signature in title
                if ex_id and _is_duplicate_report(ex_title, title, report_type, date_str):
                    try:
                        delete_note(note_id=ex_id)
                        log.info("dedup_deleted", {"note_id": ex_id, "title": ex_title})
                    except Exception as e:
                        log.warn("dedup_delete_failed", {"error": str(e), "note_id": ex_id})
        except Exception as e:
            log.warn("dedup_search_failed", {"error": str(e)})

        result = create_note(title=title, content=content)
        note_id = None
        if isinstance(result, dict):
            if result.get("ok"):
                note_id = result.get("data", {}).get("noteId")
            else:
                log.warn("create_note failed", {"error": result.get("error"), "title": title})
        if not note_id:
            return None
        try:
            add_note_to_collection(note_id=note_id, title=COLLECTION_TITLE)
        except Exception as e:
            log.warn("add_note_to_collection failed", {"error": str(e), "note_id": note_id})
        try:
            state = get_state() or {}
            state[f"latest_{report_type}"] = {
                "report_id": note_id,
                "title": title,
                "date": date_str,
                "created_at": int(time.time() * 1000),
            }
            set_state(state)
        except Exception as e:
            log.warn("set_state failed", {"error": str(e)})
        return note_id
    except Exception as e:
        log.error("save_report exception", {"error": str(e), "title": title})
        return None


def _is_duplicate_report(existing_title, new_title, report_type, date_str):
    """Return True if existing_title is a duplicate of new_title for dedup purposes."""
    if not existing_title or not new_title:
        return False
    if report_type in ("weekly", "monthly", "custom"):
        # Both should match: "📊 知识库扫描 - {date} ({label})"
        if existing_title.startswith("📊 知识库扫描") and new_title.startswith("📊 知识库扫描"):
            return date_str in existing_title  # same date = duplicate
    elif report_type == "deep_dive":
        # "🔍 专题深挖 - {topic} - {date}"
        if existing_title.startswith("🔍 专题深挖") and new_title.startswith("🔍 专题深挖"):
            # Match if same date + same topic
            ex_parts = existing_title.split(" - ")
            new_parts = new_title.split(" - ")
            return ex_parts[-1] == new_parts[-1] and (len(ex_parts) > 1 and len(new_parts) > 1 and ex_parts[1] == new_parts[1])
    elif report_type == "actions":
        if "行动清单" in existing_title and "行动清单" in new_title:
            return date_str in existing_title
    elif report_type == "connections":
        if "知识关联发现" in existing_title and "知识关联发现" in new_title:
            return date_str in existing_title
    return False


def _get_latest_state(prefer=("weekly", "monthly", "deep_dive", "connections", "actions")):
    try:
        state = get_state() or {}
    except Exception:
        return None
    for key in prefer:
        v = state.get(f"latest_{key}")
        if v and v.get("report_id"):
            # Validate the note still exists
            rid = v.get("report_id")
            content = _read_note_safe(rid)
            if content:
                return v
            else:
                log.warn("latest_state.stale", {"key": key, "report_id": rid})
    return None


# ---------------------------------------------------------------------------
# Note parsing helpers
# ---------------------------------------------------------------------------

def _extract_top_items_from_note(content):
    items = []
    if not content:
        return items
    in_top = False
    for line in content.split("\n"):
        line = line.strip()
        if "TOP 10" in line or "TOP 5" in line or "TOP 候选" in line:
            in_top = True
            continue
        if in_top:
            if line.startswith("# "):
                in_top = False
                continue
            if line.startswith("## ") and "TOP" not in line:
                in_top = False
                continue
            m = re.match(r"^(\d+)\.\s+(?:【[^】]+】)?(.+?)(?:\s+\(`(.+?)`\))?(?:\s*·\s*.*)?\s*$", line)
            if m:
                title = m.group(2).strip()
                rtype = (m.group(3) or "note").strip()
                items.append({"title": title, "type": rtype})
    return items


def _extract_connections_from_note(content):
    connections = []
    if not content:
        return connections
    in_conn = False
    current = None
    for line in content.split("\n"):
        line = line.strip()
        if "关联列表" in line:
            in_conn = True
            continue
        if in_conn:
            if line.startswith("# ") and "知识关联发现" not in line:
                in_conn = False
                continue
            if line.startswith("## ") and "关联" not in line:
                in_conn = False
                continue
            if line.startswith("### "):
                if current:
                    connections.append(current)
                strength = "medium"
                if "强" in line or "strong" in line.lower():
                    strength = "strong"
                elif "弱" in line or "weak" in line.lower():
                    strength = "weak"
                current = {"strength": strength, "reason": ""}
            elif current is not None and "原因" in line and ":" in line:
                current["reason"] = line.split(":", 1)[1].strip()
    if current:
        connections.append(current)
    return connections


def _read_note_safe(note_id):
    if not note_id:
        return None
    try:
        result = read_note(note_id=note_id)
        if isinstance(result, dict) and result.get("ok"):
            return result.get("data", {}).get("content")
    except Exception as e:
        log.error("read_note failed", {"error": str(e), "note_id": note_id})
    return None


def _strip_frontmatter(content):
    """Remove YAML frontmatter (--- ... ---) from note content."""
    if not content:
        return ""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip("\n")
    return content


def _extract_title_from_content(content):
    """Extract title from note content (first H1 or first non-empty line after frontmatter)."""
    body = _strip_frontmatter(content)
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("---") or line.startswith("noteId:") or line.startswith("category:") or line.startswith("source:") or line.startswith("updatedAt:") or line.startswith("createdAt:") or line.startswith("title:"):
            continue
        # H1 heading
        if line.startswith("# "):
            return line.lstrip("# ").strip()
        # First content line
        return line[:100]
    return ""


# ---------------------------------------------------------------------------
# Note content builders
# ---------------------------------------------------------------------------

def _build_scan_note_content(start, end, by_type, keywords, top_items, range_label, anomalies, quality_scores):
    lines = [f"# 📊 知识库扫描 - {range_label}\n"]
    lines.append(f"**时间范围**: {start} → {end}")
    lines.append(f"**总计**: {sum(by_type.values())} 条\n")
    lines.append("## 📈 类型分布\n")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        anomaly = " ⚠️" if t in anomalies else ""
        lines.append(f"- {t}: {c}{anomaly}")
    lines.append("\n## 🔑 TOP 关键词\n")
    lines.append(", ".join(keywords) or "无")
    lines.append("\n## ⭐ TOP 10 高价值条目\n")
    for i, it in enumerate(top_items, 1):
        label = it.get("label", it.get("type", "note"))
        value = it.get("value", "")
        title = it.get("title", "?")
        ttype = it.get("type", "note")
        qscore = quality_scores.get(it.get("noteId", ""), "?")
        entry = f"{i}. 【{label}】{title} (`{ttype}`) · 质量分 {qscore}"
        if value and value != "可忽略":
            entry += f"\n   💡 {value}"
        lines.append(entry)
    return "\n".join(lines)


def _build_connections_note_content(connections, top_items):
    lines = ["# 🧠 知识关联发现\n"]
    lines.append(f"**TOP 条目**: {len(top_items)} 个")
    lines.append(f"**识别关联**: {len(connections)} 组\n")
    lines.append("## TOP 候选条目\n")
    for i, it in enumerate(top_items, 1):
        label = it.get("label", it.get("type", "note"))
        lines.append(f"{i}. 【{label}】{it.get('title', '?')}")
    lines.append("\n## 🔗 关联列表\n")
    type_emoji = {
        "支撑": "🔗", "延伸": "🔄", "矛盾": "⚡",
        "互补": "🧩", "迁移": "🌉", "duplicate": "📋",
    }
    strength_emoji = {"strong": "🔥", "medium": "⚡", "weak": "💭"}
    for i, conn in enumerate(connections, 1):
        rtype = conn.get("relation_type", "")
        strength = conn.get("strength", "medium")
        reason = conn.get("reason", "?")
        source_title = conn.get("source_title", "?")
        target_title = conn.get("target_title", "?")
        emoji = type_emoji.get(rtype, strength_emoji.get(strength, "💡"))
        type_label = rtype if rtype else strength
        lines.append(f"### {i}. {emoji} {type_label.upper()}")
        lines.append(f"**条目 A**: {source_title}")
        lines.append(f"**条目 B**: {target_title}")
        lines.append(f"**原因**: {reason}\n")
    return "\n".join(lines)


def _build_actions_note_content(actions, scan_summary, connections):
    lines = ["# ✅ 行动清单\n"]
    lines.append(
        f"**基于**: {len(scan_summary.get('top_items', []))} 个 TOP 条目 + {len(connections)} 组关联\n"
    )
    type_labels = {"supplement": "📝 补充", "create": "✨ 创造", "connect": "🔗 连接"}
    emoji_map = {"P0": "🔴", "P1": "🟡", "P2": "🟢"}
    for i, act in enumerate(actions, 1):
        prio = act.get("priority", "P1")
        time_est = act.get("estimated_time", "?")
        text = act.get("action", "?")
        atype = act.get("action_type", "create")
        type_label = type_labels.get(atype, "💡")
        lines.append(f"## {i}. {emoji_map.get(prio, '💡')} {prio} · {time_est} · {type_label}")
        lines.append(f"{text}\n")
    return "\n".join(lines)


def _build_deep_dive_note_content(topic, report_data, related):
    lines = [f"# 🔍 专题深挖：{topic}\n"]
    lines.append(f"**关联笔记**: {len(related)} 条\n")
    if report_data.get("core_points"):
        lines.append("## 📌 核心要点\n")
        for pt in report_data["core_points"]:
            lines.append(f"- {pt}")
        lines.append("")
    if report_data.get("hidden_connections"):
        lines.append("## 🔗 隐藏关联\n")
        for conn in report_data["hidden_connections"]:
            lines.append(f"- {conn}")
        lines.append("")
    if report_data.get("action_suggestions"):
        lines.append("## ✅ 行动建议\n")
        for act in report_data["action_suggestions"]:
            lines.append(f"- {act}")
        lines.append("")
    lines.append("## 📚 关联笔记\n")
    for n in related[:20]:
        lines.append(f"- {n.get('title', '?')} (`{n.get('type', 'note')}`)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UI builders
# ---------------------------------------------------------------------------

def _ui_back_home():
    return {
        "kind": "button",
        "label": "← 返回主页",
        "style": "default",
        "action": {"method": "GET", "path": "/", "prompt": "返回主页"},
    }


def _build_hub_ui():
    return {
        "components": [
            {"kind": "text", "text": "🧬 知识库自我进化", "heading": 2},
            {"kind": "text", "text": "三步结构化进化：扫描 → 关联发现 → 行动清单"},
            {"kind": "divider"},
            {"kind": "text", "text": "📅 周度快照", "heading": 4},
            {"kind": "text", "text": "扫描本周新增内容 + 提取 TOP 10 高价值条目"},
            {
                "kind": "button", "label": "🚀 开始周度进化", "style": "primary",
                "action": {
                    "method": "GET", "path": "/scan_ui",
                    "params": {"range_type": "week"},
                    "prompt": "开始周度进化",
                },
            },
            {"kind": "divider"},
            {"kind": "text", "text": "📆 月度体检", "heading": 4},
            {"kind": "text", "text": "扫描近 30 天内容，检查知识库健康度"},
            {
                "kind": "button", "label": "🏥 开始月度体检", "style": "primary",
                "action": {
                    "method": "GET", "path": "/scan_ui",
                    "params": {"range_type": "month"},
                    "prompt": "开始月度体检",
                },
            },
            {"kind": "divider"},
            {"kind": "text", "text": "🔍 专题深挖", "heading": 4},
            {"kind": "text", "text": "针对特定主题深度挖掘知识关联"},
            {"kind": "input", "key": "topic", "label": "主题关键词", "placeholder": "例如：RAG, AI 内容创作, 知识管理"},
            {
                "kind": "button", "label": "🔎 深挖", "style": "primary",
                "action": {
                    "method": "POST", "path": "/deep_dive_ui",
                    "promptTemplate": "深挖 {topic}",
                },
            },
            {"kind": "divider"},
            {"kind": "text", "text": "📚 历史报告", "heading": 4},
            {
                "kind": "button", "label": "查看所有历史报告", "style": "default",
                "action": {
                    "method": "GET", "path": "/history_ui",
                    "prompt": "查看历史报告",
                },
            },
        ]
    }


def _build_scan_result_ui(start, end, items, by_type, keywords, top_items, report_id, range_label, anomalies, quality_scores):
    total = len(items)
    type_breakdown = " · ".join(
        f"{k}:{v}{' ⚠️' if k in anomalies else ''}"
        for k, v in sorted(by_type.items(), key=lambda x: -x[1])
    )
    components = [
        {"kind": "text", "text": f"📊 知识库扫描结果 ({range_label})", "heading": 2},
        {"kind": "text", "text": f"时间: {start} → {end}  |  总计: {total} 条（已过滤噪音）"},
        {"kind": "divider"},
        {"kind": "text", "text": "📈 类型分布", "heading": 3},
        {"kind": "text", "text": type_breakdown or "无数据"},
        {"kind": "divider"},
        {"kind": "text", "text": "🔑 TOP 关键词", "heading": 3},
        {"kind": "text", "text": ", ".join(keywords) or "无"},
    ]
    if anomalies:
        components.append({"kind": "divider"})
        components.append({"kind": "text", "text": "⚠️ 异常标记（周环比 >50%）", "heading": 3})
        for atype in anomalies:
            components.append({"kind": "text", "text": f"- {atype} 类型内容激增"})
    if not items:
        components.append({"kind": "text", "text": "⚠️ 本周期无有效内容（已过滤 AI 日志和空条目）"})
    else:
        components.append({"kind": "divider"})
        components.append({"kind": "text", "text": "⭐ TOP 10 高价值条目", "heading": 3})
        for i, item in enumerate(top_items, 1):
            label = item.get("label", item.get("type", "note"))
            value = item.get("value", "")
            title = item.get("title", "(无标题)")
            ttype = item.get("type", "note")
            qscore = quality_scores.get(item.get("noteId", ""), "?")
            text = f"{i}. 【{label}】{title} (`{ttype}`) · 质量分 {qscore}"
            if value and value != "可忽略":
                text += f"\n   💡 {value}"
            components.append({"kind": "text", "text": text})
    components.append({"kind": "divider"})
    if items and len(top_items) >= 2:
        components.append({
            "kind": "button", "label": "🧠 下一步：发现知识关联", "style": "primary",
            "action": {
                "method": "GET", "path": "/connections_ui",
                "params": {"report_id": report_id or ""},
                "prompt": "发现知识关联",
            },
        })
    elif items:
        components.append({"kind": "text", "text": "💡 至少需要 2 个 TOP 条目才能发现关联"})
    if report_id:
        components.append({
            "kind": "button", "label": "📄 查看已保存报告", "style": "default",
            "open_target": f"note://{report_id}",
        })
    return {"components": components}


def _build_connections_result_ui(connections, top_items, report_id):
    components = [
        {"kind": "text", "text": "🧠 知识关联发现", "heading": 2},
        {"kind": "text", "text": f"基于 {len(top_items)} 个 TOP 条目，识别 {len(connections)} 组关联"},
        {"kind": "divider"},
    ]
    if not connections:
        components.append({"kind": "text", "text": "💡 暂未发现明显关联，可以换个周期重试"})
    else:
        type_emoji = {
            "支撑": "🔗", "延伸": "🔄", "矛盾": "⚡",
            "互补": "🧩", "迁移": "🌉", "duplicate": "📋",
        }
        strength_emoji = {"strong": "🔥", "medium": "⚡", "weak": "💭"}
        for i, conn in enumerate(connections, 1):
            pair_id = conn.get("pair_id", "")
            rtype = conn.get("relation_type", "")
            strength = conn.get("strength", "medium")
            reason = conn.get("reason", "")
            a_label, b_label = "?", "?"
            a_title, b_title = "?", "?"
            a_note_id, b_note_id = "", ""
            try:
                a_idx, b_idx = pair_id.replace("p", "").split("_")
                ai, bi = int(a_idx), int(b_idx)
                if ai < len(top_items):
                    a_label = top_items[ai].get("label", "?")
                    a_title = top_items[ai].get("title", "?")
                    a_note_id = top_items[ai].get("noteId", "")
                if bi < len(top_items):
                    b_label = top_items[bi].get("label", "?")
                    b_title = top_items[bi].get("title", "?")
                    b_note_id = top_items[bi].get("noteId", "")
            except Exception:
                pass
            emoji = type_emoji.get(rtype, strength_emoji.get(strength, "💡"))
            card_actions = []
            if a_note_id:
                card_actions.append({
                    "label": "📄 A原文",
                    "open_target": f"note://{a_note_id}",
                })
            if b_note_id:
                card_actions.append({
                    "label": "📄 B原文",
                    "open_target": f"note://{b_note_id}",
                })
            type_label = rtype if rtype else strength
            components.append({
                "kind": "card",
                "title": f"{emoji} 关联 #{i} · {type_label.upper()}",
                "content": f"【{a_label}】{a_title}  ↔  【{b_label}】{b_title}\n\n原因: {reason}",
                "actions": card_actions,
            })
    components.append({"kind": "divider"})
    components.append({
        "kind": "button", "label": "✅ 下一步：生成行动清单", "style": "primary",
        "action": {
            "method": "GET", "path": "/actions_ui",
            "params": {"report_id": report_id or ""},
            "prompt": "生成行动清单",
        },
    })
    components.append(_ui_back_home())
    return {"components": components}


def _build_actions_result_ui(actions, report_id):
    """v1.0.0: Action cards with type-specific execute buttons."""
    components = [
        {"kind": "text", "text": "✅ 行动清单", "heading": 2},
        {"kind": "divider"},
    ]
    if not actions:
        components.append({"kind": "text", "text": "💡 暂未生成行动建议"})
    else:
        type_labels = {"supplement": "📝 补充", "create": "✨ 创造", "connect": "🔗 连接"}
        type_descriptions = {
            "supplement": "补充已有笔记，新建+引用原文",
            "create": "基于发现创造新笔记",
            "connect": "在两条笔记间建立关联",
        }
        emoji_map = {"P0": "🔴", "P1": "🟡", "P2": "🟢"}
        for i, act in enumerate(actions, 1):
            prio = act.get("priority", "P1")
            action_text = act.get("action", "")
            time_est = act.get("estimated_time", "")
            atype = act.get("action_type", "create")
            type_label = type_labels.get(atype, "💡")
            type_desc = type_descriptions.get(atype, "")
            emoji = emoji_map.get(prio, "💡")

            card_actions = [{
                "label": f"✅ 执行{type_label}",
                "action": {
                    "method": "POST",
                    "path": "/actions_ui",
                    "params": {
                        "execute": "true",
                        "action_index": str(i - 1),
                        "report_id": report_id or "",
                    },
                    "prompt": f"执行行动: {action_text[:50]}",
                },
            }]

            components.append({
                "kind": "card",
                "title": f"{emoji} 行动 #{i} · {prio} · {time_est} · {type_label}",
                "content": f"{action_text}\n\n_{type_desc}_",
                "actions": card_actions,
            })
    components.append({"kind": "divider"})
    components.append({
        "kind": "button", "label": "📚 查看历史报告", "style": "default",
        "action": {
            "method": "GET", "path": "/history_ui",
            "prompt": "查看历史报告",
        },
    })
    components.append(_ui_back_home())
    return {"components": components}


def _build_execute_action_ui(action, action_index, report_id, top_items):
    """v1.0.0: Show execution form based on action type."""
    atype = action.get("action_type", "create")
    action_text = action.get("action", "")
    related_note_id = action.get("related_note_id", "")
    related_note_ids = action.get("related_note_ids", [])

    components = [
        {"kind": "text", "text": f"✅ 执行行动", "heading": 2},
        {"kind": "text", "text": f"行动：{action_text}"},
        {"kind": "divider"},
    ]

    if atype == "supplement":
        # Show original note content + AI-generated supplement draft
        components.append({"kind": "text", "text": "📝 补充已有笔记", "heading": 3})
        components.append({"kind": "text", "text": "将生成补充内容并创建新笔记（引用原文，不修改原文）"})
        if related_note_id:
            components.append({
                "kind": "button", "label": "📄 查看原文", "style": "default",
                "open_target": f"note://{related_note_id}",
            })
        components.append({"kind": "input", "key": "supplement_note", "label": "补充内容", "placeholder": "AI 将自动生成补充草稿，你可以在此修改"})
        components.append({
            "kind": "button", "label": "📝 确认创建补充笔记", "style": "primary",
            "action": {
                "method": "POST",
                "path": "/actions_ui",
                "params": {
                    "execute": "true",
                    "action_index": str(action_index),
                    "report_id": report_id or "",
                    "confirm": "true",
                },
                "prompt": "确认创建补充笔记",
            },
        })

    elif atype == "connect":
        components.append({"kind": "text", "text": "🔗 建立知识关联", "heading": 3})
        components.append({"kind": "text", "text": "将创建一篇关联笔记，引用两条原文笔记"})
        if related_note_ids:
            for nid in related_note_ids[:2]:
                components.append({
                    "kind": "button", "label": f"📄 查看笔记", "style": "default",
                    "open_target": f"note://{nid}",
                })
        components.append({"kind": "input", "key": "connection_note", "label": "关联说明", "placeholder": "AI 将自动生成关联说明，你可以在此修改"})
        components.append({
            "kind": "button", "label": "🔗 确认创建关联笔记", "style": "primary",
            "action": {
                "method": "POST",
                "path": "/actions_ui",
                "params": {
                    "execute": "true",
                    "action_index": str(action_index),
                    "report_id": report_id or "",
                    "confirm": "true",
                },
                "prompt": "确认创建关联笔记",
            },
        })

    else:  # create
        components.append({"kind": "text", "text": "✨ 创造新笔记", "heading": 3})
        components.append({"kind": "text", "text": "将基于扫描发现生成结构化笔记草稿"})
        components.append({"kind": "input", "key": "creation_note", "label": "笔记草稿", "placeholder": "AI 将自动生成草稿，你可以在此修改"})
        components.append({
            "kind": "button", "label": "✨ 确认创建笔记", "style": "primary",
            "action": {
                "method": "POST",
                "path": "/actions_ui",
                "params": {
                    "execute": "true",
                    "action_index": str(action_index),
                    "report_id": report_id or "",
                    "confirm": "true",
                },
                "prompt": "确认创建新笔记",
            },
        })

    components.append({"kind": "divider"})
    components.append({
        "kind": "button", "label": "← 返回行动清单", "style": "default",
        "action": {
            "method": "GET", "path": "/actions_ui",
            "params": {"report_id": report_id or ""},
            "prompt": "返回行动清单",
        },
    })
    components.append(_ui_back_home())
    return {"components": components}


def _build_execute_success_ui(note_id, action_type, report_id):
    """v1.0.0: Show success message after action execution."""
    type_labels = {"supplement": "补充笔记", "create": "新笔记", "connect": "关联笔记"}
    type_label = type_labels.get(action_type, "笔记")
    return {
        "components": [
            {"kind": "text", "text": f"✅ {type_label}已创建", "heading": 3},
            {"kind": "text", "text": f"笔记 ID: {note_id}"},
            {
                "kind": "button", "label": "📄 查看笔记", "style": "primary",
                "open_target": f"note://{note_id}",
            },
            {
                "kind": "button", "label": "← 返回行动清单", "style": "default",
                "action": {
                    "method": "GET", "path": "/actions_ui",
                    "params": {"report_id": report_id or ""},
                    "prompt": "返回行动清单",
                },
            },
            _ui_back_home(),
        ]
    }


def _build_deep_dive_form_ui():
    return {
        "components": [
            {"kind": "text", "text": "🔍 专题深挖", "heading": 2},
            {"kind": "text", "text": "针对特定主题深度挖掘知识库中的隐藏关联"},
            {"kind": "divider"},
            {"kind": "input", "key": "topic", "label": "主题关键词", "placeholder": "例如：RAG, AI 内容创作"},
            {
                "kind": "choice", "key": "time_range", "label": "时间范围",
                "multiple": False,
                "options": [
                    {"value": "week", "label": "本周"},
                    {"value": "month", "label": "近 30 天"},
                    {"value": "all", "label": "全部时间"},
                ],
            },
            {
                "kind": "button", "label": "🔎 开始深挖", "style": "primary",
                "action": {
                    "method": "POST", "path": "/deep_dive_ui",
                    "promptTemplate": "深挖 {topic}",
                },
            },
            _ui_back_home(),
        ]
    }


def _build_deep_dive_result_ui(topic, report_data, report_id):
    components = [
        {"kind": "text", "text": f"🔍 专题深挖：{topic}", "heading": 2},
        {"kind": "divider"},
    ]
    if not report_data:
        components.append({"kind": "text", "text": "❌ 深挖报告生成失败"})
        components.append(_ui_back_home())
        return {"components": components}
    if report_data.get("title"):
        components.append({"kind": "text", "text": report_data["title"], "heading": 3})
    if report_data.get("core_points"):
        components.append({"kind": "text", "text": "📌 核心要点", "heading": 4})
        for pt in report_data["core_points"]:
            components.append({"kind": "text", "text": f"• {pt}"})
    if report_data.get("hidden_connections"):
        components.append({"kind": "divider"})
        components.append({"kind": "text", "text": "🔗 隐藏关联", "heading": 4})
        for conn in report_data["hidden_connections"]:
            components.append({"kind": "text", "text": f"• {conn}"})
    if report_data.get("action_suggestions"):
        components.append({"kind": "divider"})
        components.append({"kind": "text", "text": "✅ 行动建议", "heading": 4})
        for act in report_data["action_suggestions"]:
            components.append({"kind": "text", "text": f"• {act}"})
    if report_id:
        components.append({"kind": "divider"})
        components.append({
            "kind": "button", "label": "📄 查看完整报告", "style": "primary",
            "open_target": f"note://{report_id}",
        })
    components.append({
        "kind": "button", "label": "🔍 深挖其他主题", "style": "default",
        "action": {"method": "GET", "path": "/deep_dive_ui", "prompt": "再次深挖"},
    })
    components.append(_ui_back_home())
    return {"components": components}


def _build_history_ui(reports, filter_type):
    components = [
        {"kind": "text", "text": f"📚 历史报告 ({filter_type})", "heading": 2},
        {"kind": "text", "text": f"共 {len(reports)} 份报告"},
        {"kind": "divider"},
    ]
    if not reports:
        components.append({"kind": "text", "text": "💡 暂无历史报告"})
    else:
        emoji_map = {"weekly": "📅", "monthly": "📆", "deep_dive": "🔍"}
        for r in reports:
            rtype = r.get("type", "weekly")
            rdate = r.get("date", "?")
            rtitle = r.get("title", "?")
            rid = r.get("note_id")
            emoji = emoji_map.get(rtype, "📄")
            card_actions = []
            if rid:
                card_actions.append({
                    "label": "📄 打开",
                    "open_target": f"note://{rid}",
                })
            components.append({
                "kind": "card",
                "title": f"{emoji} {rtitle}",
                "subtitle": f"{rdate} · {rtype}",
                "actions": card_actions,
            })
    components.append({"kind": "divider"})
    components.append(_ui_back_home())
    return {"components": components}


def _build_error_ui(message):
    return {
        "components": [
            {"kind": "text", "text": f"❌ {message}", "heading": 3},
            _ui_back_home(),
        ]
    }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.route("GET", "/")
def handle_home(params):
    return _build_hub_ui()


@router.route("GET", "/scan_ui")
def handle_scan(params):
    p = params or {}
    range_type = (p.get("range_type") or "week").strip().lower()
    start_date = (p.get("start_date") or "").strip()
    end_date = (p.get("end_date") or "").strip()
    save_flag = (p.get("save") or "true").strip().lower() != "false"

    if range_type == "custom" and (not start_date or not end_date):
        return {
            "components": [
                {"kind": "text", "text": "📅 自定义时间范围", "heading": 3},
                {"kind": "input", "key": "start_date", "label": "开始日期", "placeholder": "YYYY-MM-DD"},
                {"kind": "input", "key": "end_date", "label": "结束日期", "placeholder": "YYYY-MM-DD"},
                {
                    "kind": "button", "label": "开始扫描", "style": "primary",
                    "action": {
                        "method": "GET", "path": "/scan_ui",
                        "params": {"range_type": "custom"},
                        "promptTemplate": "扫描 {start_date} 到 {end_date}",
                    },
                },
                _ui_back_home(),
            ]
        }

    start, end = _date_range_for(range_type, start_date, end_date)
    label = _range_label(range_type, start, end)
    log.info("scan.start", {"range_type": range_type, "start": start, "end": end})

    # --- Round 1: 时间排序搜索 ---
    items = _search_range(start, end, limit=200)

    # --- Phase 1: 过滤噪音 ---
    unfiltered_items = items
    items = _filter_noise(items)
    by_type = _group_by_type(items)
    keywords = _extract_keywords(items, top_n=10)

    # --- Round 2: 关键词相关性补充搜索 ---
    seen_ids = {it.get("noteId") for it in items if it.get("noteId")}
    for kw in keywords[:3]:
        extra = _search_topic(kw, "all", limit=20)
        for it in extra:
            if it.get("noteId") and it["noteId"] not in seen_ids:
                filtered = _filter_noise([it])
                if filtered:
                    items.append(filtered[0])
                    seen_ids.add(it["noteId"])

    # --- v1.0.0: 4维质量评分 ---
    quality_scores = {}
    for it in items:
        nid = it.get("noteId", "")
        if nid:
            quality_scores[nid] = _compute_quality_score(it)

    # --- Phase 3: 异常标记 ---
    unfiltered_by_type = _group_by_type(unfiltered_items)
    anomalies = _detect_anomalies(unfiltered_by_type, range_type)

    # --- v1.0.0: 质量分加权的 diversity selection ---
    # Add quality score to items for _pick_top_items to use
    for it in items:
        it["_quality"] = quality_scores.get(it.get("noteId", ""), 50)

    top_items = _pick_top_items(items, n=10, keywords=keywords)

    # --- Phase 2: LLM 语义标签 ---
    labeled = _llm_label_items(top_items)
    for i, lb in enumerate(labeled):
        if i < len(top_items):
            top_items[i]["label"] = lb.get("label", top_items[i].get("type", "note"))
            top_items[i]["value"] = lb.get("value", "")

    report_id = None
    if save_flag and items:
        report_type = "weekly" if range_type == "week" else "monthly" if range_type == "month" else "custom"
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"📊 知识库扫描 - {date_str} ({label})"
        content = _build_scan_note_content(start, end, by_type, keywords, top_items, label, anomalies, quality_scores)
        report_id = _save_report(title, content, report_type, date_str)

        # Cache top_items with noteId in state for downstream handlers
        try:
            state = get_state() or {}
            state["cached_top_items"] = [
                {"noteId": t.get("noteId", ""), "title": t.get("title", ""), "type": t.get("type", ""),
                 "label": t.get("label", ""), "value": t.get("value", ""),
                 "preview": (t.get("preview") or "")[:300]}
                for t in top_items[:10]
            ]
            set_state(state)
        except Exception as e:
            log.warn("cache_top_items failed", {"error": str(e)})

    log.info("scan.complete", {"items": len(items), "filtered": True, "report_id": report_id})
    return _build_scan_result_ui(start, end, items, by_type, keywords, top_items, report_id, label, anomalies, quality_scores)


def _detect_anomalies(current_by_type, range_type):
    if range_type != "week":
        return set()
    try:
        today = datetime.now().date()
        prev_start = (today - timedelta(days=14)).isoformat()
        prev_end = (today - timedelta(days=7)).isoformat()
        prev_items = _search_range(prev_start, prev_end, limit=200)
        prev_by_type = _group_by_type(prev_items)
        anomalies = set()
        for t, count in current_by_type.items():
            prev_count = prev_by_type.get(t, 0)
            if prev_count > 0 and count > prev_count * 1.5:
                anomalies.add(t)
        return anomalies
    except Exception as e:
        log.warn("anomaly detection failed", {"error": str(e)})
        return set()


@router.route("GET", "/connections_ui")
def handle_connections(params):
    p = params or {}
    report_id = (p.get("report_id") or "").strip()
    if not report_id:
        latest = _get_latest_state(("weekly", "monthly", "custom"))
        if latest:
            report_id = latest.get("report_id", "")
    if not report_id:
        return {
            "components": [
                {"kind": "text", "text": "⚠️ 没有可用的扫描报告", "heading": 3},
                {"kind": "text", "text": "请先运行一次扫描"},
                {
                    "kind": "button", "label": "← 开始扫描", "style": "primary",
                    "action": {
                        "method": "GET", "path": "/scan_ui",
                        "params": {"range_type": "week"},
                        "prompt": "开始扫描",
                    },
                },
                _ui_back_home(),
            ]
        }

    content = _read_note_safe(report_id)
    if not content:
        return _build_error_ui("无法读取报告")

    # Prefer cached top_items from state (has noteId), fallback to note parsing
    top_items = []
    try:
        state = get_state() or {}
        cached = state.get("cached_top_items", [])
        if cached:
            top_items = cached
    except Exception:
        pass
    if not top_items:
        top_items = _extract_top_items_from_note(content)
        # Fallback for regex miss
        if not top_items:
            for line in content.split("\n"):
                line = line.strip()
                m = re.match(r"^(\d+)\.\s+【([^】]+)】(.+)", line)
                if m:
                    top_items.append({"title": m.group(3).strip(), "type": m.group(2).strip()})
                elif re.match(r"^(\d+)\.\s+", line):
                    m2 = re.match(r"^(\d+)\.\s+(.+?)(?:\s+\(`(.+?)`\))?", line)
                    if m2 and m2.group(2).strip():
                        top_items.append({"title": m2.group(2).strip(), "type": m2.group(3) or "note"})
    if len(top_items) < 2:
        return {
            "components": [
                {"kind": "text", "text": "💡 TOP 条目不足 2 个，无法发现关联", "heading": 3},
                {
                    "kind": "button", "label": "← 重新扫描", "style": "default",
                    "action": {
                        "method": "GET", "path": "/scan_ui",
                        "params": {"range_type": "week"},
                        "prompt": "重新扫描",
                    },
                },
                _ui_back_home(),
            ]
        }

    log.info("connections.start", {"top_items": len(top_items), "report_id": report_id})

    # --- v1.0.1: 3-layer connection discovery (fixed: TF-IDF as soft rank, not hard filter) ---
    # Layer 1: TF-IDF cosine similarity as RANKING signal (not filter)
    # All pairs enter candidate pool, sorted by similarity, top 15 sent to LLM
    vocab, vectors = _build_tfidf_vectors(top_items)
    all_pairs = []
    for i in range(len(top_items)):
        for j in range(i + 1, len(top_items)):
            sim = _cosine_similarity(vectors[i], vectors[j])
            # Skip near-duplicates (>0.85 similarity)
            if sim > 0.85:
                continue
            all_pairs.append({
                "pair_id": f"p{i}_{j}",
                "sim": sim,
                "i": i,
                "j": j,
            })

    # Sort by similarity descending, take top 15 candidates for LLM
    all_pairs.sort(key=lambda x: x["sim"], reverse=True)
    candidate_pairs = []
    for p in all_pairs[:15]:
        i, j = p["i"], p["j"]
        candidate_pairs.append({
            "pair_id": p["pair_id"],
            "item_a": {
                "title": top_items[i].get("title", ""),
                "label": top_items[i].get("label", ""),
                "type": top_items[i].get("type", "note"),
                "value": top_items[i].get("value", ""),
                "preview": (top_items[i].get("preview") or "")[:200],
                "note_id": top_items[i].get("noteId", ""),
            },
            "item_b": {
                "title": top_items[j].get("title", ""),
                "label": top_items[j].get("label", ""),
                "type": top_items[j].get("type", "note"),
                "value": top_items[j].get("value", ""),
                "preview": (top_items[j].get("preview") or "")[:200],
                "note_id": top_items[j].get("noteId", ""),
            },
            "cosine_sim": round(p["sim"], 3),
        })

    # Layer 2: LLM relation classification
    connections = _llm_classify_relations(candidate_pairs, top_items)

    # v1.0.2: Fallback when LLM returns empty — use top TF-IDF pairs as heuristic connections
    if not connections and len(top_items) >= 2:
        log.warn("connections.llm_empty_fallback", {"candidates": len(candidate_pairs)})
        for p in candidate_pairs[:MAX_CONNECTIONS]:
            i, j = p["i"], p["j"]
            sim = p["sim"]
            connections.append({
                "pair_id": p["pair_id"],
                "relation_type": "互补" if sim < 0.5 else "支撑",
                "reason": f"TF-IDF 相似度 {round(sim, 2)}，标题语义接近",
            })

    # Layer 3: Cross-domain bridging
    cross = _llm_find_cross_domain(top_items, connections)
    if cross:
        connections.extend(cross)

    # Mark duplicates + enrich with source/target titles
    seen_pairs = set()
    unique_connections = []
    for conn in connections:
        pid = conn.get("pair_id", "")
        if pid in seen_pairs:
            continue
        seen_pairs.add(pid)
        if conn.get("relation_type") == "duplicate":
            continue  # skip duplicates
        # Enrich with source/target titles from pair_id
        m = re.match(r"p(\d+)_(\d+)", pid)
        if m:
            ai, bi = int(m.group(1)), int(m.group(2))
            if ai < len(top_items):
                conn["source_title"] = top_items[ai].get("title", "?")
                conn["source_note_id"] = top_items[ai].get("noteId", "")
            if bi < len(top_items):
                conn["target_title"] = top_items[bi].get("title", "?")
                conn["target_note_id"] = top_items[bi].get("noteId", "")
        unique_connections.append(conn)
    connections = unique_connections[:MAX_CONNECTIONS]

    new_id = None
    if connections:
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"🧠 知识关联发现 - {date_str}"
        note_content = _build_connections_note_content(connections, top_items)
        new_id = _save_report(title, note_content, "connections", date_str)
        # Cache connections in state for downstream actions handler
        try:
            state = get_state() or {}
            state["cached_connections"] = connections
            set_state(state)
        except Exception as e:
            log.warn("cache_connections failed", {"error": str(e)})

    log.info("connections.complete", {"count": len(connections), "cross_domain": len(cross)})
    return _build_connections_result_ui(connections, top_items, new_id or report_id)


@router.route("GET", "/actions_ui")
def handle_actions_get(params):
    """GET /actions_ui — Generate and display action list."""
    p = params or {}
    report_id = (p.get("report_id") or "").strip()

    # Try cached_actions_report_id first (from previous GET call)
    if not report_id:
        try:
            _state = get_state() or {}
            cached_rid = _state.get("cached_actions_report_id", "")
            if cached_rid:
                report_id = cached_rid
        except Exception:
            pass

    if not report_id:
        latest = _get_latest_state(("connections", "weekly", "monthly", "custom"))
        if latest:
            report_id = latest.get("report_id", "")
    if not report_id:
        return {
            "components": [
                {"kind": "text", "text": "⚠️ 没有可用的报告", "heading": 3},
                {"kind": "text", "text": "请先运行：扫描 → 关联发现"},
                {
                    "kind": "button", "label": "← 开始扫描", "style": "primary",
                    "action": {
                        "method": "GET", "path": "/scan_ui",
                        "params": {"range_type": "week"},
                        "prompt": "开始扫描",
                    },
                },
                _ui_back_home(),
            ]
        }

    content = _read_note_safe(report_id)
    if not content:
        return _build_error_ui("无法读取报告")

    connections = _extract_connections_from_note(content)
    top_items = _extract_top_items_from_note(content)

    # Fallback: if top_items empty (regex mismatch), extract from lines with 【...】
    if not top_items:
        for line in content.split("\n"):
            line = line.strip()
            m = re.match(r"^(\d+)\.\s+【([^】]+)】(.+)", line)
            if m:
                top_items.append({"title": m.group(3).strip(), "type": m.group(2).strip()})
            elif re.match(r"^(\d+)\.\s+", line):
                m2 = re.match(r"^(\d+)\.\s+(.+?)(?:\s+\(`(.+?)`\))?", line)
                if m2 and m2.group(2).strip():
                    top_items.append({"title": m2.group(2).strip(), "type": m2.group(3) or "note"})

    # --- Try to get richer data from state cache ---
    state_connections = []
    state_top_items = []
    try:
        _state = get_state() or {}
        state_connections = _state.get("cached_connections", [])
        state_top_items = _state.get("cached_top_items", [])
    except Exception:
        pass

    # Use state cache if available (has source_title, target_title, relation_type)
    enriched_connections = state_connections if state_connections else connections
    scan_summary = {
        "top_items": [{"title": t.get("title", ""), "type": t.get("type", "")} for t in top_items[:10]],
        "connections_count": len(connections),
    }

    log.info("actions.start", {"report_id": report_id, "connections": len(connections), "enriched_connections": len(enriched_connections), "top_items": len(top_items)})
    actions = _llm_generate_actions(scan_summary, enriched_connections)

    if actions:
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"✅ 行动清单 - {date_str}"
        action_content = _build_actions_note_content(actions, scan_summary, connections)
        _save_report(title, action_content, "actions", date_str)
        # Cache actions in state so POST handler doesn't regenerate
        try:
            state = get_state() or {}
            state["cached_actions"] = actions
            state["cached_actions_report_id"] = report_id
            set_state(state)
        except Exception as e:
            log.warn("cache_actions failed", {"error": str(e)})

    log.info("actions.complete", {"count": len(actions)})
    return _build_actions_result_ui(actions, report_id)


@router.route("POST", "/actions_ui")
def handle_actions_post(params):
    """POST /actions_ui — Execute an action or show execution form.

    v1.0.0: Three execution paths based on action_type.
    """
    p = params or {}
    report_id = (p.get("report_id") or "").strip()
    execute_flag = (p.get("execute") or "").strip() == "true"
    confirm_flag = (p.get("confirm") or "").strip() == "true"
    action_index = (p.get("action_index") or "").strip()

    # Prefer cached_actions_report_id (matches GET handler's cached actions)
    if not report_id:
        try:
            _state = get_state() or {}
            cached_rid = _state.get("cached_actions_report_id", "")
            if cached_rid:
                report_id = cached_rid
        except Exception:
            pass

    if not report_id:
        latest = _get_latest_state(("connections", "weekly", "monthly", "custom"))
        if latest:
            report_id = latest.get("report_id", "")
    if not report_id:
        return _build_error_ui("没有可用的报告")

    content = _read_note_safe(report_id)
    if not content:
        return _build_error_ui("无法读取报告")

    connections = _extract_connections_from_note(content)
    top_items = _extract_top_items_from_note(content)

    # Also try state cache for richer data (has noteId, titles, relation_type)
    try:
        _state = get_state() or {}
        _cached_top = _state.get("cached_top_items", [])
        _cached_conn = _state.get("cached_connections", [])
        if _cached_top and not top_items:
            top_items = _cached_top
        if _cached_conn and not connections:
            connections = _cached_conn
    except Exception:
        pass

    # Fallback: if top_items empty (regex mismatch), extract from lines with 【...】
    if not top_items:
        for line in content.split("\n"):
            line = line.strip()
            m = re.match(r"^(\d+)\.\s+【([^】]+)】(.+)", line)
            if m:
                top_items.append({"title": m.group(3).strip(), "type": m.group(2).strip()})
            elif re.match(r"^(\d+)\.\s+", line):
                m2 = re.match(r"^(\d+)\.\s+(.+?)(?:\s+\(`(.+?)`\))?", line)
                if m2 and m2.group(2).strip():
                    top_items.append({"title": m2.group(2).strip(), "type": m2.group(3) or "note"})

    # --- Resolve actions (prefer cache over regenerating) ---
    cached_report_id = None
    cached_actions = None
    try:
        state = get_state() or {}
        cached_report_id = state.get("cached_actions_report_id")
        cached_actions = state.get("cached_actions")
    except Exception:
        pass

    if cached_actions and cached_report_id == report_id:
        actions = cached_actions
        log.info("actions.post.cached", {"count": len(actions)})
    else:
        # Fallback: regenerate if cache miss
        scan_summary = {
            "top_items": [{"title": t.get("title", ""), "type": t.get("type", "")} for t in top_items[:10]],
            "connections_count": len(connections),
        }
        actions = _llm_generate_actions(scan_summary, connections)

    if not actions:
        return _build_error_ui("无法生成行动建议")

    # Resolve note titles → noteIds for supplement/connect actions
    # top_items may have noteId from connections report, but usually only title
    # Build a title→noteId lookup from top_items (if available) or search
    title_to_id = {}
    for t in top_items:
        nid = t.get("noteId", "")
        if nid:
            title_to_id[t.get("title", "")] = nid

    for act_item in actions:
        # Resolve supplement: related_note_id (title) → real noteId
        rid = act_item.get("related_note_id", "")
        if rid and rid in title_to_id:
            act_item["related_note_id"] = title_to_id[rid]
        elif rid and not rid.startswith("mp"):
            # It's a title, try search
            found = _search_topic(rid, "all", limit=1)
            if found:
                act_item["related_note_id"] = found[0].get("noteId", rid)

        # Resolve connect: related_note_ids (titles) → real noteIds
        rids = act_item.get("related_note_ids", [])
        if rids:
            resolved = []
            for rid_title in rids:
                if rid_title in title_to_id:
                    resolved.append(title_to_id[rid_title])
                elif not rid_title.startswith("mp"):
                    found = _search_topic(rid_title, "all", limit=1)
                    if found:
                        resolved.append(found[0].get("noteId", rid_title))
                    else:
                        resolved.append(rid_title)
                else:
                    resolved.append(rid_title)
            act_item["related_note_ids"] = resolved

    # Check action_index valid
    if not action_index.isdigit():
        return _build_error_ui("无效的行动索引")
    idx = int(action_index)
    if idx >= len(actions):
        return _build_error_ui("行动索引超出范围")

    act = actions[idx]
    atype = act.get("action_type", "create")

    # --- Show execution form (first click: execute=true) ---
    if execute_flag and not confirm_flag:
        return _build_execute_action_ui(act, idx, report_id, top_items)

    # --- Execute action (confirmed) ---
    if execute_flag and confirm_flag:
        date_str = datetime.now().strftime("%Y-%m-%d")
        action_text = act.get("action", "")

        # Build rich scan context for LLM
        items_context = "\n".join(
            f"- {t.get('title', '')} ({t.get('type', '')})"
            for t in top_items[:10]
        )
        conn_context = "\n".join(
            f"- [{c.get('strength', 'medium')}] {c.get('reason', '')}"
            for c in connections[:5]
        )

        if atype == "supplement":
            # Read original note, then call LLM to generate real supplement
            related_id = act.get("related_note_id", "")
            original_content = ""
            original_title = action_text[:50]
            # Fallback: if related_id empty, try searching by action keywords
            if not related_id:
                search_kw = action_text[:30].replace("补充", "").replace("内容", "").strip()
                if search_kw:
                    found = _search_topic(search_kw, "all", limit=1)
                    if found:
                        related_id = found[0].get("noteId", "")
            if related_id:
                raw = _read_note_safe(related_id)
                if raw:
                    original_content = _strip_frontmatter(raw)[:3000]
                    extracted_title = _extract_title_from_content(raw)
                    if extracted_title:
                        original_title = extracted_title

            log.info("action.supplement.llm_start", {"related_id": related_id, "title": original_title})
            draft = _llm_draft_supplement(original_title, original_content) if (original_content or original_title) else ""
            if not draft:
                # Fallback: generate based on action description alone
                draft = _llm_draft_creation(f"补充：{action_text}", items_context, related_contents=[{"title": original_title, "content": original_content[:2000]}] if original_content else None) or "（AI 无法生成补充内容，请手动编辑）"

            note_title = f"📝 补充：{original_title[:50]}"
            supplement_content = f"# {note_title}\n\n"
            supplement_content += f"> 补充说明：{action_text}\n"
            supplement_content += f"> 创建时间：{date_str}\n\n"
            supplement_content += f"## 补充内容\n\n{draft}\n\n"
            if related_id:
                supplement_content += f"---\n\n**参考原文**: [{original_title}](note://{related_id})\n"

            note_id = _save_report(note_title, supplement_content, "supplement", date_str)
            if note_id:
                log.info("action.supplement.created", {"note_id": note_id, "related": related_id})
                return _build_execute_success_ui(note_id, "supplement", report_id)
            return _build_error_ui("创建补充笔记失败")

        elif atype == "connect":
            # Call LLM to generate real connection analysis
            related_ids = act.get("related_note_ids", [])
            item_a_title, item_a_preview = "", ""
            item_b_title, item_b_preview = "", ""
            item_a_id, item_b_id = "", ""
            # Ensure at least 2 related_ids — fallback search if missing
            if len(related_ids) < 2 and top_items:
                for t in top_items:
                    nid = t.get("noteId", "")
                    if nid and nid not in related_ids:
                        related_ids.append(nid)
                        if len(related_ids) >= 2:
                            break
            if len(related_ids) >= 2:
                item_a_id, item_b_id = related_ids[0], related_ids[1]
                a_raw = _read_note_safe(related_ids[0]) or ""
                b_raw = _read_note_safe(related_ids[1]) or ""
                if a_raw:
                    a_title = _extract_title_from_content(a_raw)
                    item_a_title = a_title if a_title else related_ids[0]
                    item_a_preview = _strip_frontmatter(a_raw)[:300]
                else:
                    item_a_title = related_ids[0]
                if b_raw:
                    b_title = _extract_title_from_content(b_raw)
                    item_b_title = b_title if b_title else related_ids[1]
                    item_b_preview = _strip_frontmatter(b_raw)[:300]
                else:
                    item_b_title = related_ids[1]
            else:
                # Fallback: extract from action_text
                item_a_title = action_text[:50]
                item_b_title = ""

            log.info("action.connect.llm_start", {"ids": related_ids})
            draft = _llm_draft_connection(item_a_title, item_a_preview, item_b_title, item_b_preview)
            if not draft:
                # Fallback: use action description as content
                draft = f"{action_text}\n\n**条目 A**: {item_a_title}\n\n**条目 B**: {item_b_title}\n\n（AI 无法生成更深入的关联分析，请手动补充）"

            note_title = f"🔗 关联：{item_a_title[:20]} ↔ {item_b_title[:20]}"
            conn_content = f"# {note_title}\n\n"
            conn_content += f"> 关联说明：{action_text}\n"
            conn_content += f"> 创建时间：{date_str}\n\n"
            conn_content += f"## 关联分析\n\n{draft}\n\n"
            conn_content += f"---\n\n**关联笔记**:\n"
            if item_a_id:
                conn_content += f"- [{item_a_title}](note://{item_a_id})\n"
            if item_b_id:
                conn_content += f"- [{item_b_title}](note://{item_b_id})\n"

            note_id = _save_report(note_title, conn_content, "connection", date_str)
            if note_id:
                log.info("action.connect.created", {"note_id": note_id, "related": related_ids})
                return _build_execute_success_ui(note_id, "connect", report_id)
            return _build_error_ui("创建关联笔记失败")

        else:  # create
            # v1.0.3: Read related notes for richer context
            related_contents = []
            # Strategy 1: search by action keywords to find most relevant notes
            # Extract core topic from action text like "基于「xxx」创造一篇知识整合笔记"
            import re
            topic_match = re.search(r'[\u300c\u300e"\'\"](.+?)[\u300d\u300f"\'\"]', action_text)
            if topic_match:
                search_kws = topic_match.group(1)[:50]
            else:
                # Strip common action verbs/particles, keep nouns
                search_kws = re.sub(r'(基于|创造|创建|写|生成|一份|一篇|笔记|知识整合)', '', action_text[:50]).strip()
            log.info("action.create.search_kws", {"search_kws": search_kws, "raw_action": action_text[:80]})
            if search_kws:
                found_notes = _search_topic(search_kws, "all", limit=2)
                for fn in found_notes[:2]:
                    fn_id = fn.get("noteId", "")
                    if fn_id:
                        fn_raw = _read_note_safe(fn_id)
                        if fn_raw:
                            fn_title = _extract_title_from_content(fn_raw) or fn.get("title", "")
                            fn_content = _strip_frontmatter(fn_raw)[:2000]
                            related_contents.append({"title": fn_title, "content": fn_content})
            # Strategy 2: read action's related_note_ids (from LLM action generation)
            if not related_contents:
                for rid in act.get("related_note_ids", [])[:2]:
                    if rid:
                        rid_raw = _read_note_safe(rid)
                        if rid_raw:
                            rid_title = _extract_title_from_content(rid_raw) or rid
                            rid_content = _strip_frontmatter(rid_raw)[:2000]
                            related_contents.append({"title": rid_title, "content": rid_content})
            # Strategy 3: read top 1 note from scan results
            if not related_contents and top_items:
                ti = top_items[0]
                ti_id = ti.get("noteId", "")
                if ti_id:
                    ti_raw = _read_note_safe(ti_id)
                    if ti_raw:
                        ti_title = _extract_title_from_content(ti_raw) or ti.get("title", "")
                        ti_content = _strip_frontmatter(ti_raw)[:2000]
                        related_contents.append({"title": ti_title, "content": ti_content})

            scan_ctx = f"TOP 条目:\n{items_context}\n\n关联发现:\n{conn_context}"
            log.info("action.create.llm_start", {"action": action_text[:50], "related_contents": len(related_contents)})
            draft = _llm_draft_creation(action_text, scan_ctx, related_contents)
            if not draft:
                draft = f"（AI 无法生成草稿内容，请手动编辑）\n\n原始行动：{action_text}"

            note_title = f"✨ {action_text[:50]}"
            create_content = f"# {note_title}\n\n"
            create_content += f"> 基于知识库扫描发现\n"
            create_content += f"> 创建时间：{date_str}\n\n"
            create_content += f"{draft}\n"

            note_id = _save_report(note_title, create_content, "creation", date_str)
            if note_id:
                log.info("action.create.created", {"note_id": note_id})
                return _build_execute_success_ui(note_id, "create", report_id)
            return _build_error_ui("创建笔记失败")

    # Fallback: show action list
    return _build_actions_result_ui(actions, report_id)


@router.route("GET", "/deep_dive_ui")
def handle_deep_dive_get(params):
    return _build_deep_dive_form_ui()


@router.route("POST", "/deep_dive_ui")
def handle_deep_dive(params):
    p = params or {}
    topic = (p.get("topic") or p.get("input") or "").strip()
    time_range = (p.get("time_range") or "all").strip().lower()
    save_flag = (p.get("save") or "true").strip().lower() != "false"

    if not topic:
        return _build_deep_dive_form_ui()

    log.info("deep_dive.start", {"topic": topic, "time_range": time_range})
    related = _search_topic(topic, time_range, limit=30)
    related = _filter_noise(related)

    if not related:
        return {
            "components": [
                {"kind": "text", "text": f"🔍 未找到与「{topic}」相关的笔记", "heading": 3},
                {"kind": "text", "text": "试试其他关键词，或者换个时间范围"},
                _ui_back_home(),
            ]
        }

    report_data = _llm_deep_dive(topic, related)
    if not report_data:
        return {
            "components": [
                {"kind": "text", "text": f"❌ 深挖报告生成失败: {topic}", "heading": 3},
                {
                    "kind": "button", "label": "← 重试", "style": "default",
                    "action": {"method": "GET", "path": "/deep_dive_ui", "prompt": "重试"},
                },
            ]
        }

    report_id = None
    if save_flag:
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"🔍 专题深挖 - {topic} - {date_str}"
        note_content = _build_deep_dive_note_content(topic, report_data, related)
        report_id = _save_report(title, note_content, "deep_dive", date_str)

    log.info("deep_dive.complete", {"topic": topic, "related": len(related), "report_id": report_id})
    return _build_deep_dive_result_ui(topic, report_data, report_id)


@router.route("GET", "/history_ui")
def handle_history(params):
    p = params or {}
    filter_type = (p.get("type") or "all").strip().lower()
    try:
        limit = int(p.get("limit") or 20)
    except ValueError:
        limit = 20

    log.info("history.start", {"filter_type": filter_type, "limit": limit})
    items = []
    try:
        result = search_notes({
            "collection": COLLECTION_TITLE,
            "sort_by": "created",
            "limit": max(limit * 2, 50),
        })
        if isinstance(result, dict) and result.get("ok"):
            items = result.get("data", {}).get("results", []) or []
    except Exception as e:
        log.error("history.search failed", {"error": str(e)})

    title_prefix_to_type = {
        "📊": "weekly", "🧠": "connections", "✅": "actions", "🔍": "deep_dive",
    }
    reports = []
    for it in items:
        title = it.get("title", "")
        matched = None
        for prefix, rtype in title_prefix_to_type.items():
            if title.startswith(prefix):
                matched = rtype
                break
        if not matched:
            continue
        if matched == "weekly" and "近 30 天" in title:
            matched = "monthly"
        if filter_type == "all":
            if matched in ("connections", "actions"):
                continue
            reports.append({
                "type": matched,
                "title": title,
                "date": (it.get("createdAt") or "")[:10],
                "note_id": it.get("noteId"),
            })
        elif matched == filter_type:
            reports.append({
                "type": matched,
                "title": title,
                "date": (it.get("createdAt") or "")[:10],
                "note_id": it.get("noteId"),
            })

    reports = reports[:limit]
    log.info("history.complete", {"count": len(reports)})
    return _build_history_ui(reports, filter_type)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handle(request):
    try:
        return router.handle(request)
    except Exception as e:
        log.error("unhandled", {"error": str(e)})
        return _build_error_ui(f"内部错误: {e}")
