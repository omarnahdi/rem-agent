import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    import discord  # noqa: F401
    import rem.moderation as moderation
except ModuleNotFoundError as exc:
    if exc.name in {"discord", "groq", "dotenv"}:
        moderation = None
    else:
        raise


@unittest.skipIf(moderation is None, "Install project dependencies with `uv sync` to run moderation integration unit tests.")
class ModerationFailurePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_classifier_label_is_unavailable_not_safe(self):
        result = await moderation._parse_moderation_response(
            '{"classification":"MAYBE","confidence":0.99,"reason":"invalid label"}'
        )
        self.assertEqual(result["classification"], "UNAVAILABLE")
        self.assertEqual(result["confidence"], 0.99)

    async def test_gemini_and_groq_failures_return_unavailable_not_safe(self):
        def fail_generate_content(**kwargs):
            raise RuntimeError("Gemini unavailable")

        gemini = SimpleNamespace(models=SimpleNamespace(generate_content=fail_generate_content))
        author = SimpleNamespace(
            id=710733071993012237,
            display_name="Guest",
            name="guest",
            mention="<@710733071993012237>",
            bot=False,
        )
        target = SimpleNamespace(
            id=992390617575477268,
            display_name="NightxWolf",
            name="NightxWolf",
            mention="<@992390617575477268>",
            bot=False,
        )
        message = SimpleNamespace(author=author, content="please review this message")
        with patch.object(
            moderation,
            "_groq_moderation_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Groq unavailable"),
        ):
            result = await moderation.classify_message(gemini, message, target)
        self.assertEqual(result["classification"], "UNAVAILABLE")
        self.assertEqual(result["confidence"], 0.0)
        self.assertIn("unavailable", result["reason"].casefold())


if __name__ == "__main__":
    unittest.main()
