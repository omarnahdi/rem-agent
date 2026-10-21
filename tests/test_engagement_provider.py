import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rem.schemas import EngagementDecision
from rem.agent import build_engagement_evaluator


class EngagementProviderSelectionTests(unittest.TestCase):
    def test_gemini_is_selected_even_when_groq_key_is_configured(self):
        settings = SimpleNamespace(
            gemini_api_key="gemini-test-key",
            gemini_engagement_model="gemini-3.8-flash",
            groq_api_key="groq-test-key",
        )

        with (
            patch("rem.agent.Gemini") as gemini,
            patch("rem.agent.Groq") as groq,
            patch("rem.agent.Agent", return_value="evaluator-agent") as agent,
        ):
            result = build_engagement_evaluator(settings)

        self.assertEqual(result, "evaluator-agent")
        gemini.assert_called_once_with(
            id="gemini-3.8-flash",
            api_key="gemini-test-key",
            temperature=0,
            max_output_tokens=512,
        )
        groq.assert_not_called()
        agent_kwargs = agent.call_args.kwargs
        self.assertIs(agent_kwargs["output_schema"], EngagementDecision)
        self.assertNotIn("use_json_mode", agent_kwargs)
        self.assertFalse(agent_kwargs["markdown"])

    def test_missing_gemini_key_fails_loudly_instead_of_silently_using_groq(self):
        settings = SimpleNamespace(
            gemini_api_key=None,
            gemini_engagement_model="gemini-3.8-flash",
            groq_api_key="groq-test-key",
        )
        with patch("rem.agent.Groq") as groq:
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                build_engagement_evaluator(settings)
        groq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
