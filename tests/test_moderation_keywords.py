"""Dependency-free checks that legacy moderation terms remain in the project."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from rem.moderation_policy import must_block_conversation_agent

ROOT = Path(__file__).resolve().parents[1]
MODERATION_SOURCE = ROOT / "src" / "rem" / "moderation.py"


def load_literal_assignment(name: str):
    tree = ast.parse(MODERATION_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"Could not find literal assignment {name}")


class ModerationPolicyTests(unittest.TestCase):
    def test_unavailable_classification_blocks_conversation_agent(self):
        self.assertTrue(must_block_conversation_agent("UNAVAILABLE"))
        self.assertTrue(must_block_conversation_agent(" unavailable "))

    def test_valid_classifications_are_not_blocked_by_failure_policy(self):
        for classification in ("SAFE", "MILD_INSULT", "ABUSE"):
            self.assertFalse(must_block_conversation_agent(classification))
        self.assertTrue(must_block_conversation_agent(None))


class AutoTimeoutDurationTests(unittest.TestCase):
    def test_master_protection_timeout_is_ten_minutes(self):
        self.assertEqual(load_literal_assignment("AUTO_TIMEOUT_MASTER_MINUTES"), 10)

    def test_second_general_abuse_penalty_is_twenty_minutes(self):
        self.assertEqual(load_literal_assignment("AUTO_TIMEOUT_GENERAL_MINUTES"), 20)


class LegacyModerationKeywordTests(unittest.TestCase):
    def test_legacy_master_aliases_are_preserved(self):
        aliases = set(load_literal_assignment("MASTER_TEXT_ALIASES"))
        for alias in (
            "nightxwolf", "nightx wolf", "omar", "omar nahdi", "umar",
            "nalla", "nallaa", "berojgaar", "berojgar", "master",
        ):
            self.assertIn(alias, aliases)

    def test_legacy_fast_abuse_patterns_are_preserved(self):
        patterns = set(load_literal_assignment("MASTER_FAST_ABUSE_PATTERNS"))
        for pattern in (
            r"\bgandu\b", r"\bgaandu\b", r"\bchutiya\b", r"\bchutiye\b",
            r"\bmadarchod\b", r"\bmaderchod\b", r"\bbhenchod\b",
            r"\bbhosdi[a-z]*\b", r"\bbhosdike\b", r"\blavde\b",
            r"\bbsdk\b", r"\bmkc\b", r"\bdalla\b", r"\bdalle\b",
            r"\bfuck\s+(?:you|u)\b", r"\bfuck\s+off\b", r"\bpiece\s+of\s+shit\b",
            r"\basshole\b", r"\bdumbass\b",
        ):
            self.assertIn(pattern, patterns)

    def test_rand_variants_are_in_the_legacy_fast_abuse_keyword_list(self):
        patterns = load_literal_assignment("MASTER_FAST_ABUSE_PATTERNS")
        self.assertIn(r"\brand\b", patterns)
        self.assertIn(r"\br\.a\.n\.d\b", patterns)

        # The moderation code case-folds the message before applying these
        # patterns, so both uppercase user spellings are covered.
        import re
        joined = " ".join(patterns)
        for abusive_keyword in ("RAND", "R.A.N.D", "RAND.", "R.A.N.D.", "rand", "r.a.n.d"):
            with self.subTest(keyword=abusive_keyword):
                self.assertTrue(
                    any(re.search(pattern, abusive_keyword.casefold()) for pattern in patterns),
                    msg=f"Keyword variant was not matched: {abusive_keyword}",
                )

        # Do not trigger on larger unrelated words such as BRAND or R.A.N.DY.
        for unrelated in ("brand", "randolph", "r.a.n.dylan"):
            with self.subTest(unrelated=unrelated):
                self.assertFalse(
                    any(re.search(pattern, unrelated.casefold()) for pattern in patterns),
                    msg=f"Unexpected substring match: {unrelated}",
                )


if __name__ == "__main__":
    unittest.main()
