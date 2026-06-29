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


if __name__ == "__main__":
    unittest.main()
