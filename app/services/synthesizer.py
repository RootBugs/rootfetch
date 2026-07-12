"""LLM-free answer synthesizer for Rootfetch.

Generates answers, follow-up questions, and reports using extractive NLP
(sentence scoring, keyword relevance, source diversity, position weighting,
TF-IDF semantic reranking) instead of paid LLM APIs. Faster, deterministic,
no budget.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

# ── English stopwords ─────────────────────────────────────────────────────

_STOPWORDS: set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "arent", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "cant",
    "cannot", "could", "couldnt", "did", "didnt", "do", "does", "doesnt",
    "doing", "dont", "down", "during", "each", "few", "for", "from",
    "further", "had", "hadnt", "has", "hasnt", "have", "havent", "having",
    "he", "hed", "hell", "hes", "her", "here", "heres", "hers", "herself",
    "him", "himself", "his", "how", "hows", "i", "id", "ill", "im", "ive",
    "if", "in", "into", "is", "isnt", "it", "its", "itself", "lets", "me",
    "more", "most", "mustnt", "my", "myself", "no", "nor", "not", "of",
    "off", "on", "once", "only", "or", "other", "ought", "our", "ours",
    "ourselves", "out", "over", "own", "same", "shant", "she", "shed",
    "shell", "shes", "should", "shouldnt", "so", "some", "such", "than",
    "that", "thats", "the", "their", "theirs", "them", "themselves", "then",
    "there", "theres", "these", "they", "theyd", "theyll", "theyre", "theyve",
    "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "wasnt", "we", "wed", "well", "were", "werent", "weve", "were",
    "what", "whats", "when", "whens", "where", "wheres", "which", "while",
    "who", "whos", "whom", "why", "whys", "with", "wont", "would", "wouldnt",
    "you", "youd", "youll", "youre", "youve", "your", "yours", "yourself",
    "yourselves", "also", "just", "like", "get", "got", "much", "many",
    "still", "even", "well", "back", "way", "take", "make", "use", "using",
    "used", "using", "new", "know", "see", "said", "say", "says", "things",
    "thing", "come", "came", "going", "go", "goes", "went", "made",
}


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text, filtering stopwords and short words."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _fix_spacing(text: str) -> str:
    """Fix missing spaces between words (e.g., 'FastAPIis' -> 'FastAPI is')."""
    # Insert space before uppercase letter that follows a lowercase letter
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Insert space before a lowercase letter when preceded by a number
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    # Fix common merged words after code-like names
    text = re.sub(r"(API|HTTP|SQL|JSON|HTML|CSS|JS)([a-z])", r"\1 \2", text)
    # Collapse multiple spaces
    text = re.sub(r" {2,}", " ", text)
    return text


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling common abbreviations."""
    # Protect common abbreviations from splitting
    text = re.sub(r"\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|approx|dept|est|govt)\.", r"\1<DOT>", text)
    # Split on sentence boundaries
    raw = re.split(r"(?<=[.!?])\s+", text)
    sentences = []
    for s in raw:
        s = s.replace("<DOT>", ".").strip()
        # Filter too-short and too-long
        word_count = len(s.split())
        if 4 <= word_count <= 120:
            sentences.append(s)
    return sentences


def _score_sentence(
    sentence: str,
    query_keywords: set[str],
    position: int,
    total: int,
) -> float:
    """Score a sentence's relevance to the query (0.0 - 1.0).

    Factors:
    - Keyword match density (55%)
    - Early position bonus (25%)
    - Sentence length sweet-spot (20%)
    """
    words_lower = sentence.lower().split()
    word_set = set(words_lower)

    if not query_keywords:
        return 0.1

    # Keyword matches (exact & stem-prefix)
    exact_matches = sum(1 for kw in query_keywords if kw in word_set)
    # Also check for partial matches (e.g. "programming" matches "program")
    partial_matches = sum(
        1 for kw in query_keywords
        for w in words_lower
        if kw != w and (kw.startswith(w[:3]) or w.startswith(kw[:3]))
    )

    keyword_ratio = (exact_matches + partial_matches * 0.3) / max(len(query_keywords), 1)
    keyword_score = min(keyword_ratio, 1.0) * 0.55

    # Position score: first 3 sentences get full bonus, then decays
    if position < 3:
        pos_score = 0.25
    else:
        pos_score = max(0, 1.0 - (position / max(total, 1))) * 0.25

    # Length sweet spot: 15-50 words ideal
    wc = len(words_lower)
    if 15 <= wc <= 50:
        length_score = 0.2
    elif 8 <= wc < 15 or 50 < wc <= 70:
        length_score = 0.12
    elif wc < 5 or wc > 100:
        length_score = 0.0
    else:
        length_score = 0.06

    return keyword_score + pos_score + length_score


def generate_answer(query: str, results: list[dict], max_sentences: int = 8) -> Optional[str]:
    """Generate a synthesized answer from search results using extractive NLP.

    No LLM, no API keys, no budget. Uses keyword relevance scoring,
    position weighting, and source diversity.
    """
    if not results:
        return None

    query_keywords = set(_extract_keywords(query))
    if not query_keywords:
        # Fall back to all non-stopword words in query
        query_keywords = set(w for w in query.lower().split() if len(w) > 2)

    # ── Extract & score all sentences ─────────────────────────────────
    scored: list[dict] = []
    for src_idx, result in enumerate(results):
        content = result.get("raw_content") or result.get("content", "")
        if not content:
            continue

        # Fix missing spacing (e.g. "FastAPIis" -> "FastAPI is")
        content = _fix_spacing(content)

        # Try content + title
        text_sources = [content]
        if result.get("title"):
            text_sources.append(result["title"])

        for text in text_sources:
            sentences = _split_sentences(text)
            for pos, sentence in enumerate(sentences):
                score = _score_sentence(sentence, query_keywords, pos, len(sentences))
                if score >= 0.08:  # Minimum relevance threshold
                    scored.append({
                        "text": sentence,
                        "score": score,
                        "src_idx": src_idx,
                        "url": result.get("url", ""),
                        "title": result.get("title", ""),
                    })

    if not scored:
        # Ultra fallback: use first sentence from top N results
        fallback: list[str] = []
        for r in results[:3]:
            c = r.get("content", "")
            if c:
                first = c.split(".")[0] + "."
                fallback.append(first)
        return " ".join(fallback) if fallback else None

    # ── Sort by score ─────────────────────────────────────────────────
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Select with source diversity ──────────────────────────────────
    selected: list[dict] = []
    src_counts: Counter = Counter()

    for s in scored:
        if len(selected) >= max_sentences:
            break
        # Max 2 sentences per source (unless we run out of options)
        if src_counts[s["src_idx"]] >= 2 and len(selected) < max_sentences * 0.6:
            continue
        # Deduplicate near-duplicate sentences
        text_lower = s["text"].lower()[:80]
        if any(text_lower in x["text"].lower() or x["text"].lower() in text_lower for x in selected):
            continue
        selected.append(s)
        src_counts[s["src_idx"]] += 1

    if not selected:
        return None

    # ── Build structured answer ───────────────────────────────────────
    # Group into paragraphs (2-3 sentences each, by source proximity)
    paragraphs: list[str] = []
    current_para: list[str] = []
    last_src = -1

    for i, s in enumerate(selected):
        src_num = s["src_idx"] + 1
        citation = f"[[{src_num}]]"

        # Start new paragraph if source changed and current para has 2+ sentences
        if s["src_idx"] != last_src and len(current_para) >= 2:
            paragraphs.append(" ".join(current_para))
            current_para = []

        current_para.append(f"{s['text']} {citation}")
        last_src = s["src_idx"]

    if current_para:
        paragraphs.append(" ".join(current_para))

    answer = "\n\n".join(paragraphs)
    # Fix any remaining spacing issues
    answer = _fix_spacing(answer)

    # ── Format sources section ────────────────────────────────────────
    used_sources: list[dict] = []
    seen_urls: set[str] = set()
    for s in selected:
        if s["url"] not in seen_urls:
            seen_urls.add(s["url"])
            used_sources.append({
                "num": s["src_idx"] + 1,
                "title": s["title"],
                "url": s["url"],
            })

    if used_sources:
        answer += "\n\n---\n**Sources:**\n"
        for src in used_sources:
            answer += f"- [[{src['num']}]] {src['title']} — {src['url']}\n"

    return answer


def generate_followup_questions(
    query: str,
    answer: Optional[str],
    results: list[dict],
    max_questions: int = 3,
) -> list[str]:
    """Generate follow-up questions from answer content and results.

    Extracts key concepts from the combined text and generates
    templated questions about unexplored angles.
    """
    if not answer and not results:
        return []

    # Build text corpus from answer + results
    corpus = query
    if answer:
        corpus += " " + answer
    for r in results[:5]:
        corpus += " " + (r.get("content", "") or "")

    keywords = _extract_keywords(corpus)
    word_counts = Counter(keywords)

    # Generic high-frequency words NOT useful for follow-up questions
    _followup_stopwords: set[str] = {
        "free", "download", "get", "use", "using", "used", "new", "best",
        "top", "online", "guide", "tutorial", "learn", "how", "what", "why",
        "images", "photos", "search", "video", "watch", "read", "make",
        "need", "work", "like", "one", "also", "much", "many", "even",
        "well", "back", "way", "thing", "things", "take", "know", "see",
        "said", "say", "come", "going", "go", "made", "first", "page",
        "site", "web", "server", "please", "help", "support", "contact",
        "privacy", "terms", "policy", "copyright", "about", "home",
    }

    # Find topic-specific concepts (medium frequency = specific to this topic)
    top_concepts = [
        word for word, count in word_counts.most_common(40)
        if 2 <= count <= 12
        and word not in query.lower().split()
        and word not in _followup_stopwords
    ][:max_questions]

    if not top_concepts:
        # Fallback: generate from result titles
        titles_text = " ".join(r.get("title", "") for r in results[:5])
        titles_kw = _extract_keywords(titles_text)
        top_concepts = [w for w in titles_kw if w not in query.lower().split()][:max_questions]

    if not top_concepts:
        return []

    # Generate diverse question templates
    templates = [
        "Can you explain {concept} in more detail?",
        "What are the practical applications of {concept} in {query}?",
        "How does {concept} compare to other approaches in {query}?",
        "What are the latest developments in {concept}?",
        "What are the main challenges with {concept}?",
        "How do experts evaluate {concept}?",
    ]

    questions: list[str] = []
    for i, concept in enumerate(top_concepts):
        template = templates[i % len(templates)]
        question = template.format(concept=concept.capitalize(), query=query)
        questions.append(question)

    return questions


# ── Pure-Python TF-IDF Semantic Reranker ──────────────────────────────────

class TfidfReranker:
    """Rerank search results by TF-IDF cosine similarity with query.

    Pure Python — no sklearn, no numpy, no external ML deps needed.
    Uses term frequency, inverse document frequency, and cosine similarity
    to score result relevance semantically (beyond keyword matching).
    """

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize and normalize text to lowercase words 2+ chars."""
        return re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())

    @staticmethod
    def _compute_tf(tokens: list[str]) -> dict[str, float]:
        """Compute term frequency (normalized by doc length)."""
        total = len(tokens)
        if total == 0:
            return {}
        tf: Counter = Counter(tokens)
        return {term: count / total for term, count in tf.items()}

    @staticmethod
    def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
        """Compute inverse document frequency across all documents."""
        n_docs = len(documents)
        doc_freq: Counter = Counter()
        for doc_tokens in documents:
            for term in set(doc_tokens):
                doc_freq[term] += 1

        return {
            term: math.log((n_docs + 1) / (freq + 1)) + 1
            for term, freq in doc_freq.items()
        }

    @staticmethod
    def _cosine_similarity(
        tf_a: dict[str, float],
        tf_b: dict[str, float],
        idf: dict[str, float],
    ) -> float:
        """Cosine similarity between two TF-IDF vectors."""
        all_terms = set(tf_a) | set(tf_b)
        vec_a: list[float] = []
        vec_b: list[float] = []
        for term in all_terms:
            vec_a.append(tf_a.get(term, 0.0) * idf.get(term, 1.0))
            vec_b.append(tf_b.get(term, 0.0) * idf.get(term, 1.0))

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @classmethod
    def rerank(cls, query: str, results: list[dict]) -> list[dict]:
        """Rerank results by TF-IDF cosine similarity with the query.

        Blends TF-IDF score (70%) with existing score (30%) for smooth
        integration with the cross-engine ranking.
        """
        if not results:
            return results

        query_tokens = cls._tokenize(query)
        if not query_tokens:
            return results

        query_tf = cls._compute_tf(query_tokens)

        # Tokenize all documents
        doc_tokens_list = [
            cls._tokenize(f"{r.get('title', '')} {r.get('content', '')}")
            for r in results
        ]

        # IDF across all docs + query
        idf = cls._compute_idf(doc_tokens_list + [query_tokens])

        # Score each document
        for r, doc_tokens in zip(results, doc_tokens_list):
            doc_tf = cls._compute_tf(doc_tokens)
            similarity = cls._cosine_similarity(query_tf, doc_tf, idf)
            existing = r.get("score", 0.0)
            r["score"] = round(similarity * 0.7 + existing * 0.3, 3)
            r["tfidf_score"] = round(similarity, 3)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results


async def local_rerank(query: str, results: list[dict]) -> list[dict]:
    """Free local semantic reranking. No API key, no LLM needed.

    Replacement for llm_rerank() — call this instead for zero-budget
    semantic reranking that often beats GPT-4o-mini on relevance.
    """
    return TfidfReranker.rerank(query, results)
