import math
import unittest

from tsundokensaku.token_estimate import (
    CJK_TOKENS_PER_CHAR,
    OTHER_TOKENS_PER_CHAR,
    TextStats,
    count_text_stats,
    estimate_tokens,
)


class CountTextStatsTest(unittest.TestCase):
    def test_japanese_only_counts_all_chars_as_cjk(self) -> None:
        stats = count_text_stats("これはテスト用の日本語の文章です")
        self.assertEqual(stats.other_chars, 0)
        self.assertEqual(stats.cjk_chars, len("これはテスト用の日本語の文章です"))

    def test_english_only_counts_all_chars_as_other(self) -> None:
        stats = count_text_stats("This is a sample English sentence")
        self.assertEqual(stats.cjk_chars, 0)
        self.assertEqual(stats.other_chars, len("This is a sample English sentence"))

    def test_mixed_japanese_and_english_splits_by_char(self) -> None:
        stats = count_text_stats("Pythonでprint関数を使う")
        self.assertGreater(stats.cjk_chars, 0)
        self.assertGreater(stats.other_chars, 0)
        self.assertEqual(stats.cjk_chars + stats.other_chars, len("Pythonでprint関数を使う"))

    def test_empty_string_returns_zero_stats(self) -> None:
        self.assertEqual(count_text_stats(""), TextStats(cjk_chars=0, other_chars=0))

    def test_whitespace_only_normalizes_to_empty(self) -> None:
        self.assertEqual(count_text_stats("   \t  \n  "), TextStats(cjk_chars=0, other_chars=0))

    def test_newlines_collapse_into_single_space_boundary(self) -> None:
        # 改行はホワイトスペース正規化で1文字の空白に潰れる（otherに1文字分計上）
        collapsed = count_text_stats("こんにちは\n\nWorld")
        single_space = count_text_stats("こんにちは World")
        self.assertEqual(collapsed, single_space)

    def test_symbols_count_as_other(self) -> None:
        stats = count_text_stats("1,234.56 (税込み)")
        # 全角括弧や日本語の一部はCJK扱いだが、半角記号・数字はotherに入る
        self.assertGreater(stats.other_chars, 0)
        self.assertIn("税込み", "1,234.56 (税込み)")

    def test_fullwidth_alphanumeric_counts_as_cjk(self) -> None:
        stats = count_text_stats("ＡＢＣ１２３")
        self.assertEqual(stats.cjk_chars, len("ＡＢＣ１２３"))
        self.assertEqual(stats.other_chars, 0)


class EstimateTokensTest(unittest.TestCase):
    def test_uses_documented_coefficients(self) -> None:
        stats = TextStats(cjk_chars=10, other_chars=20)
        expected = math.ceil(10 * CJK_TOKENS_PER_CHAR + 20 * OTHER_TOKENS_PER_CHAR)
        self.assertEqual(estimate_tokens(stats), expected)

    def test_zero_stats_yields_zero_tokens(self) -> None:
        self.assertEqual(estimate_tokens(TextStats(cjk_chars=0, other_chars=0)), 0)

    def test_rounds_up_fractional_token_count(self) -> None:
        # other_chars=1 -> 0.25トークン、切り上げで1トークンになる
        self.assertEqual(estimate_tokens(TextStats(cjk_chars=0, other_chars=1)), 1)

    def test_coefficients_match_design_values(self) -> None:
        self.assertEqual(CJK_TOKENS_PER_CHAR, 1.0)
        self.assertEqual(OTHER_TOKENS_PER_CHAR, 0.25)


if __name__ == "__main__":
    unittest.main()
