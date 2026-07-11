from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass


CJK_TOKENS_PER_CHAR = 1.0
OTHER_TOKENS_PER_CHAR = 0.25

# 概算方式のバージョン名。API応答の "estimator" フィールドに載せ、将来モデル別
# トークナイザーへ差し替えた際に概算の出所を区別できるようにする（設計書 8.5）。
ESTIMATOR_NAME = "char-class-v1"

# ひらがな・カタカナ（぀-ヿ）、CJK統合漢字拡張A（㐀-䶿）、
# CJK統合漢字（一-鿿）、CJK記号・約物（　-〿）、
# 全角英数・半角カナ等の半角/全角形ブロック（＀-￯）。
# 完全なトークナイザーではなく、文字種の比率から日英混在文書でも極端に外れにくい
# 概算を得るための近似（詳細は docs/ai-export-optimization-design.md 8.1）。
_CJK_PATTERN = re.compile(
    "[぀-ヿ㐀-䶿一-鿿　-〿＀-￯]"
)


@dataclass(frozen=True)
class TextStats:
    cjk_chars: int
    other_chars: int


TokenEstimator = Callable[[TextStats], int]


def count_text_stats(text: str) -> TextStats:
    normalized = " ".join(text.split())
    if not normalized:
        return TextStats(cjk_chars=0, other_chars=0)

    cjk_chars = len(_CJK_PATTERN.findall(normalized))
    other_chars = len(normalized) - cjk_chars
    return TextStats(cjk_chars=cjk_chars, other_chars=other_chars)


def estimate_tokens(stats: TextStats) -> int:
    return math.ceil(stats.cjk_chars * CJK_TOKENS_PER_CHAR + stats.other_chars * OTHER_TOKENS_PER_CHAR)
