import json
import unittest

from pydantic import ValidationError

from rem.engagement import _decision_value
from rem.schemas import EngagementDecision, ModrinthProjectResult, ModrinthSearchOutput


class PydanticStructuredOutputTests(unittest.TestCase):
    def test_engagement_decision_is_validated_pydantic_model(self):
        result = EngagementDecision.model_validate({"decision": "JOIN"})
        self.assertEqual(result.decision, "JOIN")
        self.assertTrue(_decision_value(result))

    def test_engagement_decision_parses_json_fallback_result(self):
        self.assertTrue(_decision_value('{"decision":"JOIN"}'))
        self.assertFalse(_decision_value('{"decision":"IGNORE"}'))

    def test_engagement_decision_rejects_unstructured_or_extra_fields(self):
        self.assertIsNone(_decision_value("undefined"))
        self.assertIsNone(_decision_value("JOIN because this may help"))
        with self.assertRaises(ValidationError):
            EngagementDecision.model_validate({"decision": "JOIN", "explanation": "extra"})

    def test_modrinth_project_requires_canonical_url_for_project_type(self):
        valid = ModrinthProjectResult.model_validate({
            "title": "Sodium",
            "project_type": "mod",
            "url": "https://modrinth.com/mod/sodium?utm_source=test",
        })
        self.assertEqual(valid.url, "https://modrinth.com/mod/sodium")
        with self.assertRaises(ValidationError):
            ModrinthProjectResult.model_validate({
                "title": "Sodium",
                "project_type": "mod",
                "url": "https://modrinth.com/mod/sodium)**",
            })
        with self.assertRaises(ValidationError):
            ModrinthProjectResult.model_validate({
                "title": "Sodium",
                "project_type": "mod",
                "url": "https://modrinth.com/shader/sodium",
            })

    def test_modrinth_search_output_is_consistent_json_data(self):
        result = ModrinthSearchOutput(
            ok=True, query="sodium", requested_project_type="mod",
            results=[ModrinthProjectResult(title="Sodium", project_type="mod", url="https://modrinth.com/mod/sodium")],
            source="Modrinth public API",
        )
        dumped = result.model_dump(mode="json", exclude_none=True)
        self.assertEqual(dumped["results"][0]["url"], "https://modrinth.com/mod/sodium")
        self.assertEqual(json.loads(result.model_dump_json())["ok"], True)


if __name__ == "__main__":
    unittest.main()
