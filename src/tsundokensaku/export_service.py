from __future__ import annotations

from pathlib import Path

from tsundokensaku import database
from tsundokensaku import pdf_export
from tsundokensaku.database import get_pack, get_pack_items
from tsundokensaku.export_profiles import ExportProfile, resolve_profile
from tsundokensaku.export_stats import ItemStats, collect_item_stats, summarize_item_stats
from tsundokensaku.pdf_outline import list_chapters
from tsundokensaku.token_estimate import ESTIMATOR_NAME, estimate_tokens


EXTERNALLY_AVAILABLE_EXPORT_PROFILES = frozenset({"standard", "chat", "chapter"})


def resolve_external_profile(name: str | None) -> ExportProfile:
    """profile名を解決する。FastAPI非依存。

    不明なprofile名、または存在するが外部非公開のprofileはValueErrorを送出する。
    メッセージはいずれも解決名（profile_name/profile.name）そのものであり、
    呼び出し側（web.py）が同一の400文言へ変換できる。
    """
    profile = resolve_profile(name)
    if profile.name not in EXTERNALLY_AVAILABLE_EXPORT_PROFILES:
        raise ValueError(profile.name)
    return profile


def resolve_export_format(profile: ExportProfile, requested_format: str | None) -> str:
    """formatの既定値解決とvalidationを行う。FastAPI非依存。

    format省略時はprofile.primary_format、それも無ければ"pdf"を既定にする。
    pdf/md/json以外、またはprofileの固定formatと不一致な場合はValueErrorを送出する。
    メッセージは現行HTTP detailと同一文言にする。
    """
    resolved_format = requested_format
    if resolved_format is None:
        resolved_format = profile.primary_format if profile.primary_format is not None else "pdf"

    if resolved_format not in ("pdf", "md", "json"):
        raise ValueError("format は pdf, md, または json を指定してください")

    if profile.primary_format is not None and resolved_format != profile.primary_format:
        raise ValueError(
            f"profile={profile.name} では format={profile.primary_format} のみ指定できます"
        )

    return resolved_format


def _export_preview_warning(code: str, *, item_id: int | None, message: str) -> dict[str, object]:
    return {"code": code, "item_id": item_id, "message": message}


def build_export_preview_warnings(item_stats: list[ItemStats]) -> list[dict[str, object]]:
    if not item_stats:
        return [_export_preview_warning("empty_pack", item_id=None, message="この資料には資料項目がありません")]

    warnings: list[dict[str, object]] = []
    for entry in item_stats:
        item = entry.item
        if entry.missing_pdf:
            warnings.append(
                _export_preview_warning(
                    "missing_pdf", item_id=item.id, message=f"「{item.title}」はPDFファイルが見つかりません"
                )
            )
            continue
        if not item.pages.strip():
            warnings.append(
                _export_preview_warning(
                    "missing_pages", item_id=item.id, message=f"「{item.title}」はページが指定されていません"
                )
            )
            continue
        if not entry.page_numbers:
            warnings.append(
                _export_preview_warning(
                    "invalid_pages", item_id=item.id, message=f"「{item.title}」のページ指定を解釈できませんでした"
                )
            )
            continue
        if entry.unindexed_pages > 0:
            warnings.append(
                _export_preview_warning(
                    "unindexed_pages",
                    item_id=item.id,
                    message=f"「{item.title}」は未インデックスのため{entry.unindexed_pages}ページ分を概算に含めていません",
                )
            )
    return warnings


def _preview_base_stats(item_stats: list[ItemStats]) -> dict[str, object]:
    summary = summarize_item_stats(item_stats)
    return {
        "estimation": "approximate",
        "estimator": ESTIMATOR_NAME,
        "book_count": summary.book_count,
        "item_count": summary.item_count,
        "total_pages": summary.total_pages,
        "estimated_chars": summary.combined_stats.cjk_chars + summary.combined_stats.other_chars,
        "estimated_tokens": estimate_tokens(summary.combined_stats),
    }


def build_export_preview_payload(item_stats: list[ItemStats]) -> dict[str, object]:
    # Phase 3A からの既存レスポンス形式（profile未指定・profile=standard用）。
    # フィールド集合・値とも Phase 3C 導入前から不変（設計書12.1の後方互換方針）
    return {
        **_preview_base_stats(item_stats),
        "warnings": build_export_preview_warnings(item_stats),
    }


def build_export_preview_payload_for_profile(
    item_stats: list[ItemStats],
    profile: ExportProfile,
    *,
    pack_name: str,
    chapter_loader=None,
) -> dict[str, object]:
    """standard以外（chat等）向けの拡張プレビュー。設計書12.1のchunks付きレスポンス。

    実エクスポート（archiveオーケストレーション）と同じ plan() / chunk_filename() を
    呼ぶことで、分冊結果・ファイル名・警告をエクスポート実行前に一致させる。
    """
    item_warnings = build_export_preview_warnings(item_stats)

    if not item_stats:
        return {
            "profile": profile.name,
            **_preview_base_stats(item_stats),
            "file_count": 0,
            "archive": "zip",
            "chunks": [],
            "warnings": item_warnings,
        }

    plan = profile.plan(item_stats, chapter_loader=chapter_loader)
    # primary_format を持たないプロファイル（standardのみ）はこの関数の対象外のため
    # 実際には使われないが、chunk_filename の型契約上フォーマット文字列が必要
    format_for_naming = profile.primary_format or "pdf"

    chunks_payload = [
        {
            "filename": profile.chunk_filename(chunk, pack_name=pack_name, format=format_for_naming),
            "estimated_tokens": chunk.estimated_tokens,
            "pages": chunk.total_pages,
            "items": [
                {
                    "item_id": fragment.item.id,
                    "title": fragment.item.title,
                    "pdf_path": fragment.item.pdf_path,
                    "pages": fragment.page_spec,
                    "label": fragment.label,
                    "fragment_index": fragment.fragment_index,
                    "fragment_count": fragment.fragment_count,
                    "estimated_tokens": estimate_tokens(fragment.stats),
                }
                for fragment in chunk.fragments
            ],
        }
        for chunk in plan.chunks
    ]
    plan_warnings = [
        {"code": warning.code, "item_id": warning.item_id, "message": warning.message}
        for warning in plan.warnings
    ]

    return {
        "profile": profile.name,
        **_preview_base_stats(item_stats),
        "file_count": len(plan.chunks),
        "archive": "zip",
        "chunks": chunks_payload,
        "warnings": item_warnings + plan_warnings,
    }


def _open_pack_connection(db_path: Path):
    connection = database.connect(db_path)
    database.ensure_pack_schema(connection)
    return connection


def build_pack_export_preview(
    pack_id: int,
    *,
    profile_name: str | None,
    db_path: Path,
    books_dir: Path,
) -> dict[str, object] | None:
    """previewのDB read/close/plan生成という現行ユースケース進行。FastAPI非依存。

    pack不在は`None`で表す（webが404へ変換する）。resolve_external_profileの
    ValueError、previewのchapter_loaderが送出しうるPdfSourceNotFoundErrorは
    捕捉せずそのまま伝播させる（webが変換する）。
    """
    resolved_profile = resolve_external_profile(profile_name)

    connection = _open_pack_connection(db_path)
    try:
        pack = get_pack(connection, pack_id)
        if pack is None:
            return None
        items = get_pack_items(connection, pack_id)
        item_stats = collect_item_stats(connection, items, books_dir=books_dir)
    finally:
        connection.close()

    if not resolved_profile.uses_plan_output:
        return build_export_preview_payload(item_stats)

    chapter_loader = None
    if resolved_profile.needs_chapter_loader:
        chapter_loader = lambda pdf_path: list_chapters(
            pdf_export.resolve_pdf_source(str(pdf_path), books_dir)
        )

    return build_export_preview_payload_for_profile(
        item_stats,
        resolved_profile,
        pack_name=pack.name,
        chapter_loader=chapter_loader,
    )
