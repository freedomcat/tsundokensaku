from __future__ import annotations

from typing import Any


def build_tomorrow_actions(results: list[dict[str, Any]], query: str, limit: int = 3) -> list[dict[str, str | None]]:
    actions: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()

    for result in results:
        action = _build_action_from_result(result, query)
        if action is None:
            continue

        key = (action["kind"] or "", action["source_title"] or action["title"] or "")
        if key in seen:
            continue

        actions.append(action)
        seen.add(key)
        if len(actions) >= limit:
            return actions[:limit]

    if len(actions) < limit:
        actions.extend(_fallback_actions(query, limit - len(actions)))

    return actions[:limit]


def _build_action_from_result(result: dict[str, Any], query: str) -> dict[str, str | None] | None:
    kind = str(result.get("kind") or "").strip()
    title = str(result.get("title") or "").strip()
    if not title:
        return None

    page_summary = str(result.get("page_summary") or "").strip()
    page_number = result.get("page_number")
    snippet = str(result.get("snippet") or "").strip()
    open_url = result.get("open_url")
    scrapbox_url = result.get("scrapbox_url")

    if kind == "pdf":
        if page_summary:
            detail = f"『{title}』の {page_summary} を読む"
        elif page_number is not None:
            detail = f"『{title}』の p.{page_number} を読む"
        else:
            detail = f"『{title}』の本文を読む"
        href = open_url or scrapbox_url
    elif kind == "memo":
        detail = f"Scrapboxメモ『{title}』を開く"
        href = scrapbox_url or open_url
    elif kind == "note":
        detail = f"『{title}』のノートを確認する"
        href = scrapbox_url or open_url
    elif kind == "kindle":
        detail = f"『{title}』のメモを確認する"
        href = scrapbox_url or open_url
    else:
        detail = f"『{title}』を見直す"
        href = open_url or scrapbox_url

    reason = snippet or f"検索結果の{kind or '候補'}"
    if query.strip() and query.strip() not in reason and kind == "pdf":
        reason = f"{reason} / 検索語: {query.strip()}"

    return {
        "kind": kind or "result",
        "title": detail,
        "detail": reason,
        "href": href if isinstance(href, str) and href else None,
        "source_title": title,
    }


def _fallback_actions(query: str, count: int) -> list[dict[str, str | None]]:
    normalized = query.strip()
    fallback_templates = [
        "『{query}』をタイトルのみで再検索する",
        "『{query}』を本文のみで再検索する",
        "上位結果を順に開いて抜粋を確認する",
    ]

    actions: list[dict[str, str | None]] = []
    for template in fallback_templates:
        if len(actions) >= count:
            break
        if normalized:
            title = template.format(query=normalized)
        else:
            title = "検索語を入れて再検索する"
        actions.append(
            {
                "kind": "fallback",
                "title": title,
            "detail": "検索結果が少なかったため、次の一手として補完しました",
                "href": None,
                "source_title": "",
            }
        )
    return actions
