import unittest
from unittest.mock import patch

from tsundokensaku import tokenizer


class FakeMorpheme:
    def __init__(self, surface: str, dictionary_form: str) -> None:
        self._surface = surface
        self._dictionary_form = dictionary_form

    def surface(self) -> str:
        return self._surface

    def dictionary_form(self) -> str:
        return self._dictionary_form


class FakeSudachiTokenizer:
    def __init__(self) -> None:
        self.mode = None

    def tokenize(self, text: str, mode: object) -> list[FakeMorpheme]:
        self.mode = mode
        return [
            FakeMorpheme("走った", "走る"),
            FakeMorpheme("ＡＰＩ", "API"),
            FakeMorpheme("サーバー", "サーバー"),
        ]


class TokenizerTest(unittest.TestCase):
    def test_sudachi_tokens_use_dictionary_form_and_shared_normalization(self) -> None:
        fake = FakeSudachiTokenizer()
        with patch.object(tokenizer, "_sudachi_tokenizer", return_value=(fake, "A")):
            tokens = tokenizer.tokenize_text("走った ＡＰＩ サーバー")

        self.assertEqual(tokens, ["走る", "api", "サーバ"])
        self.assertEqual(fake.mode, "A")

    def test_fallback_tokens_normalize_width_case_and_trailing_long_vowel(self) -> None:
        with patch.object(tokenizer, "_sudachi_tokenizer", return_value=None):
            self.assertEqual(tokenizer.tokenize_text("ＦＴＳ５ Server"), ["fts5", "server"])
            self.assertEqual(tokenizer.tokenize_text("サーバー"), ["サー", "ーバ"])


class QueryHighlightTermsTest(unittest.TestCase):
    def test_excludes_operators_and_excluded_terms(self) -> None:
        terms = tokenizer.query_highlight_terms('Ruby -Rails "Martin Fowler"')

        self.assertIn("Ruby", terms)
        self.assertIn("Martin Fowler", terms)
        self.assertIn("MartinFowler", terms)
        self.assertNotIn("Rails", terms)
        self.assertNotIn("-Rails", terms)
        self.assertTrue(all('"' not in term and not term.startswith("-") for term in terms))

    def test_exclusion_only_query_yields_no_terms(self) -> None:
        self.assertEqual(tokenizer.query_highlight_terms("-Linux"), [])
        self.assertEqual(tokenizer.query_highlight_terms('-"Ruby on Rails"'), [])

    def test_build_excerpt_centers_on_phrase_without_quotes(self) -> None:
        text = "x" * 300 + " The Cathedral and the Bazaar について論じる。" + "y" * 300

        excerpt = tokenizer.build_excerpt(text, '"The Cathedral and the Bazaar"')

        self.assertIn("The Cathedral and the Bazaar", excerpt)

    def test_build_excerpt_ignores_excluded_terms(self) -> None:
        text = "a" * 300 + " Rails の話。" + "b" * 300

        excerpt = tokenizer.build_excerpt(text, "-Rails")

        # 除外語は候補にならないため、先頭からのフォールバック抜粋になる
        self.assertNotIn("Rails", excerpt)


if __name__ == "__main__":
    unittest.main()
