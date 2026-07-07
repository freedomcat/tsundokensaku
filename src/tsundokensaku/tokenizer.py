from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[ぁ-んァ-ヴー一-龥々]+")
_PROLONGED_SOUND_MARK = "ー"


@dataclass(frozen=True)
class QueryTerm:
    text: str
    phrase: bool = False
    exclude: bool = False


def parse_query(query: str) -> list[QueryTerm]:
    """検索クエリを語単位に分解する。

    - 空白区切りの各チャンクが1語
    - "..." で囲むとフレーズ（語順・隣接を保った一致）
    - 先頭 - で除外（-語 / -"フレーズ"）

    検索本体（database.py）とハイライト・抜粋（web.py / build_excerpt）が
    同じ解析結果を共有する。演算子（- と "）は text に含まれない。
    """
    parts = re.findall(r'-?"[^"]*"|\S+', query)
    terms: list[QueryTerm] = []
    for part in parts:
        exclude = part.startswith("-")
        if exclude:
            part = part[1:]
        phrase = len(part) >= 2 and part.startswith('"') and part.endswith('"')
        text = part.strip('"').strip()
        if not text:
            continue
        terms.append(QueryTerm(text=text, phrase=phrase, exclude=exclude))
    return terms


def query_highlight_terms(query: str) -> list[str]:
    """ハイライト・抜粋用の検索語候補。除外語と演算子を含まない。

    長い候補（フレーズ原文）から順に並べ、続けてトークンを返す。
    """
    include_terms = [term for term in parse_query(query) if not term.exclude]
    candidates: list[str] = []
    for term in include_terms:
        for candidate in (term.text, term.text.replace(" ", "")):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    for term in include_terms:
        for token in tokenize_text(term.text):
            if token and token not in candidates:
                candidates.append(token)
    return candidates


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text.replace("\x00", ""))
    return _WHITESPACE_RE.sub(" ", normalized).strip()


@lru_cache(maxsize=1)
def _sudachi_tokenizer():
    try:
        from sudachipy import dictionary, tokenizer as sudachi_tokenizer_module
    except ImportError:
        return None

    return dictionary.Dictionary().create(), sudachi_tokenizer_module.Tokenizer.SplitMode.A


def tokenize_text(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    sudachi = _sudachi_tokenizer()
    if sudachi is not None:
        tokenizer, mode = sudachi
        tokens = []
        for morpheme in tokenizer.tokenize(normalized, mode):
            token = _normalize_token(_morpheme_dictionary_form(morpheme))
            if token:
                tokens.append(token)
        return tokens

    return _fallback_tokens(normalized)


def tokenize_query(query: str) -> list[str]:
    parts = re.findall(r'"[^"]+"|\S+', query)
    tokens: list[str] = []
    for part in parts:
        part = part.strip('"')
        if not part:
            continue
        tokens.extend(tokenize_text(part))
    return tokens


def prepare_index_text(text: str) -> str:
    return " ".join(tokenize_text(text))


def prepare_query_text(query: str) -> str:
    tokens = tokenize_query(query)
    if tokens:
        return " ".join(f'"{token}"' for token in tokens)
    return ""


def normalize_trigram_text(text: str) -> str:
    return normalize_text(text).casefold()


def build_excerpt(text: str, query: str, *, width: int = 120) -> str:
    display_text = normalize_text(text)
    if not display_text:
        return ""

    compact_text = display_text.replace(" ", "")
    candidates = query_highlight_terms(query)

    for candidate in candidates:
        for haystack in (display_text, compact_text):
            if not haystack or not candidate:
                continue
            index = haystack.find(candidate)
            if index == -1:
                continue
            start = max(0, index - width)
            end = min(len(haystack), index + len(candidate) + width)
            excerpt = haystack[start:end]
            if start > 0:
                excerpt = f"…{excerpt}"
            if end < len(haystack):
                excerpt = f"{excerpt}…"
            return excerpt

    if len(display_text) <= width * 2:
        return display_text
    return f"{display_text[: width * 2 - 1]}…"


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in _TOKEN_RE.findall(text):
        normalized_chunk = _normalize_token(chunk)
        if not normalized_chunk:
            continue
        if normalized_chunk.isascii():
            tokens.append(normalized_chunk)
            continue
        if len(normalized_chunk) <= 2:
            tokens.append(normalized_chunk)
            continue
        tokens.extend(normalized_chunk[index : index + 2] for index in range(len(normalized_chunk) - 1))
    return tokens


def _morpheme_dictionary_form(morpheme) -> str:
    dictionary_form = morpheme.dictionary_form().strip()
    if dictionary_form and dictionary_form != "*":
        return dictionary_form
    return morpheme.surface().strip()


def _normalize_token(token: str) -> str:
    normalized = unicodedata.normalize("NFKC", token).casefold().strip()
    normalized = _WHITESPACE_RE.sub("", normalized)
    if len(normalized) > 1:
        without_trailing_marks = normalized.rstrip(_PROLONGED_SOUND_MARK)
        if without_trailing_marks:
            normalized = without_trailing_marks
    return normalized
