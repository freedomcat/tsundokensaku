import unittest

from tsundokensaku.actions import build_tomorrow_actions


class BuildTomorrowActionsTest(unittest.TestCase):
    def test_prefers_pdf_note_and_memo_hits(self) -> None:
        actions = build_tomorrow_actions(
            [
                {
                    "kind": "pdf",
                    "title": "伝わるコードレビュー",
                    "page_summary": "p.12, p.15",
                    "page_number": 12,
                    "snippet": "レビューの観点を整理する",
                    "open_url": "/pdf/book.pdf#page=12",
                    "scrapbox_url": "https://example.com/book",
                },
                {
                    "kind": "memo",
                    "title": "コードレビューのメモ",
                    "snippet": "この本はあとで読む",
                    "open_url": "https://scrapbox.io/example/コードレビューのメモ",
                    "scrapbox_url": "https://scrapbox.io/example/コードレビューのメモ",
                },
                {
                    "kind": "note",
                    "title": "読書ノート",
                    "snippet": "確認したい論点",
                    "open_url": "https://scrapbox.io/example/読書ノート",
                    "scrapbox_url": "https://scrapbox.io/example/読書ノート",
                },
            ],
            "コードレビュー",
        )

        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["title"], "『伝わるコードレビュー』の p.12, p.15 を読む")
        self.assertEqual(actions[0]["href"], "/pdf/book.pdf#page=12")
        self.assertIn("レビューの観点", actions[0]["detail"])
        self.assertEqual(actions[1]["title"], "Scrapboxメモ『コードレビューのメモ』を開く")
        self.assertEqual(actions[2]["title"], "『読書ノート』のノートを確認する")

    def test_adds_fallback_actions_when_results_are_short(self) -> None:
        actions = build_tomorrow_actions([], "SQLite", limit=3)

        self.assertEqual(len(actions), 3)
        self.assertTrue(actions[0]["title"].startswith("『SQLite』をタイトルのみで再検索する"))


if __name__ == "__main__":
    unittest.main()
