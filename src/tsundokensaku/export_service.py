from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from typing import Callable

from tsundokensaku import database
from tsundokensaku import pdf_export
from tsundokensaku.database import PackItemRecord, PackRecord, get_pack, get_pack_items
from tsundokensaku.export_profiles import ExportProfile, RenderContext, resolve_profile
from tsundokensaku.export_stats import ItemStats, collect_item_stats, summarize_item_stats
from tsundokensaku.pdf_outline import list_chapters
from tsundokensaku.token_estimate import ESTIMATOR_NAME, TextStats, estimate_tokens
from tsundokensaku.zip_export import (
    PackExportEntry,
    PlanManifestChunk,
    PlanManifestFragment,
    build_pack_zip,
    build_pack_zip_with_manifest,
    render_plan_manifest,
    sanitize_filename_component,
)


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


def record_export_event_best_effort(
    *,
    db_path: Path,
    pack_id: int | None,
    pack_name: str,
    profile: str,
    format: str,
    items: list[PackItemRecord],
) -> None:
    """書き出し成功後の履歴記録をベストエフォートで試みる。FastAPI非依存。

    `_open_pack_connection(db_path)`（schema保証込み）で別接続を開いて
    `database.record_export_event`を呼び、接続をcloseする。
    接続生成・schema保証・記録・close、いずれの段階で例外が起きても外へ
    伝播させず、ログ出力のみ行う（呼び出し元のレスポンスを壊さない）。
    """
    try:
        connection = _open_pack_connection(db_path)
        try:
            database.record_export_event(
                connection,
                pack_id=pack_id,
                pack_name=pack_name,
                profile=profile,
                format=format,
                items=items,
            )
        finally:
            connection.close()
    except Exception:
        logging.exception("export_events の記録に失敗しました（エクスポート本体は正常）")


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


@dataclass(frozen=True)
class PreparedJsonExport:
    content: bytes
    filename: str


def prepare_json_export(
    pack: PackRecord,
    items: list[PackItemRecord],
    *,
    exported_at: datetime,
) -> PreparedJsonExport:
    """JSON export（version 3）の準備。FastAPI非依存。

    itemsのJSON構造への組み立て・bytes化・filename決定を行う。webがResponse生成・
    Content-Dispositionへの変換を担う（設計書§12.3の分担）。空pack・PDF不在・
    pages不正でも検証せずそのまま生成する現状の性質を維持する（設計書§18.5）。
    時刻は呼び出し元が解決したJST時刻(`exported_at`)のみを使い、ここでは
    `datetime.now()`相当を呼ばない。
    """
    export_data = {
        "version": 3,
        "name": pack.name,
        "items": [
            {
                "pdf_path": item.pdf_path,
                "title": item.title,
                "pages": item.pages,
                "collapsed": item.collapsed,
                "addedAt": item.added_at,
                "position": item.position,
            }
            for item in items
        ],
    }
    content = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"{sanitize_filename_component(pack.name)}_{exported_at:%Y%m%d}.json"
    return PreparedJsonExport(content=content, filename=filename)


def _placeholder_item_stats_for_export(item) -> ItemStats:
    """StandardProfile.plan() へ渡す最小限のItemStats。

    standard は chunk_limit=None のため item_weight は使われず、plan() は
    1項目=1チャンクの構造（position順）を作るだけに使う。実際のページ数・
    本文検証・レンダリングは render_chunk 内で既存の render_pdf_export /
    render_markdown_export が行うため、ここでは重複計算しない
    （collect_item_stats の寛容なエラー処理をそのまま使うと、不正な
    ページ範囲の詳細なエラーメッセージが失われ後方互換性が壊れるため
    採用していない）。
    """
    return ItemStats(
        item=item,
        page_numbers=[],
        stats=TextStats(cjk_chars=0, other_chars=0),
        unindexed_pages=0,
        missing_pdf=False,
    )


@dataclass(frozen=True)
class PreparedArchiveExport:
    content: bytes
    filename: str


def prepare_archive_export(
    pack: PackRecord,
    items: list[PackItemRecord],
    *,
    format: str,
    profile: ExportProfile,
    exported_at: datetime,
    db_path: Path,
    books_dir: Path,
    render_markdown: Callable[[Path, str], tuple[str, str]],
) -> PreparedArchiveExport:
    """archive（ZIP）exportの準備。FastAPI非依存。

    空pack・pages未指定は`ValueError`、PDF不在は`pdf_export.PdfSourceNotFoundError`
    をそのまま送出し捕捉しない（webが400/404へ変換する。design.md決定1）。
    `resolve_pdf`・`chapter_loader`・`render_pdf`は`pdf_export`の非HTTP関数を
    直接使う（design.md決定2）。`render_markdown`は`_now_jst()`呼び出し回数の
    既存契約（web.py固有のクロック呼び出し）を保つため、呼び出し元（web.py）が
    構築したcallbackをそのまま受け取る。
    """
    if not items:
        raise ValueError("資料が空です")

    for item in items:
        if not item.pages.strip():
            raise ValueError(f"{item.title}: ページを指定してください")

    if profile.chunk_limit() is not None:
        connection = _open_pack_connection(db_path)
        try:
            item_stats = collect_item_stats(connection, items, books_dir=books_dir)
        finally:
            connection.close()
    else:
        item_stats = [_placeholder_item_stats_for_export(item) for item in items]

    chapter_loader = None
    if profile.needs_chapter_loader:
        chapter_loader = lambda pdf_path: list_chapters(
            pdf_export.resolve_pdf_source(str(pdf_path), books_dir)
        )

    plan = profile.plan(item_stats, chapter_loader=chapter_loader)
    ctx = RenderContext(
        pack_name=pack.name,
        exported_at=exported_at,
        format=format,
        resolve_pdf=lambda pdf_path: pdf_export.resolve_pdf_source(pdf_path, books_dir),
        render_pdf=pdf_export.render_pdf_export,
        render_markdown=render_markdown,
        total_chunks=len(plan.chunks),
    )

    entries: list[PackExportEntry] = []
    manifest_chunks: list[tuple[str, list[tuple[str, str]]] | PlanManifestChunk] = []
    for chunk in plan.chunks:
        filename = profile.chunk_filename(chunk, pack_name=pack.name, format=format)
        primary_fragment = chunk.fragments[0]
        entries.append(
            PackExportEntry(
                index=chunk.index,
                title=primary_fragment.item.title,
                page_label=primary_fragment.page_spec,
                filename=filename,
                content=profile.render_chunk(chunk, ctx),
            )
        )
        if profile.manifest_uses_fragment_labels:
            manifest_chunks.append(
                PlanManifestChunk(
                    filename=filename,
                    fragments=[
                        PlanManifestFragment(
                            title=fragment.item.title,
                            pages=fragment.page_spec,
                            label=fragment.label,
                        )
                        for fragment in chunk.fragments
                    ],
                )
            )
        else:
            manifest_chunks.append(
                (filename, [(fragment.item.title, fragment.page_spec) for fragment in chunk.fragments])
            )

    if not profile.uses_plan_output:
        # 現行 manifest（PackExportEntry 前提、1項目=1エントリ）をそのまま使い
        # バイト互換を守る（設計書 10.3）
        zip_bytes = build_pack_zip(pack_name=pack.name, entries=entries, exported_at=exported_at)
    else:
        # 複数項目チャンク（chat の分冊・chapter の結合）でも項目内訳が
        # 失われないよう、ExportPlan から組み立てた manifest を使う。
        # plan の警告（item_exceeds_limit 等）もここに記載する（設計書 14）
        manifest = render_plan_manifest(
            pack_name=pack.name,
            exported_at=exported_at,
            profile_name=profile.name,
            chunks=manifest_chunks,
            header_lines=profile.manifest_header_lines(plan),
            warnings=[warning.message for warning in plan.warnings],
        )
        zip_bytes = build_pack_zip_with_manifest(entries=entries, manifest=manifest)

    zip_filename = profile.archive_filename(pack_name=pack.name, exported_at=exported_at)
    return PreparedArchiveExport(content=zip_bytes, filename=zip_filename)
