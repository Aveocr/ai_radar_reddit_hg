import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///data/test.db")

from fastapi.testclient import TestClient

from main import app


class UserInterestsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_categories_endpoint_returns_defaults_without_data(self):
        response = self.client.get("/api/v1/categories")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(len(payload), 0)
        self.assertIn("slug", payload[0])
        self.assertIn("item_count", payload[0])

    def test_session_and_interests_flow(self):
        session_response = self.client.post("/api/v1/user/session")
        self.assertEqual(session_response.status_code, 200)
        user = session_response.json()
        self.assertIn("id", user)
        self.assertFalse(user["profile"]["onboarding_completed"])

        save_response = self.client.put(
            "/api/v1/user/interests",
            json={"categories": ["NLP", "CV"]},
        )
        self.assertEqual(save_response.status_code, 200)
        saved = save_response.json()
        self.assertTrue(saved["profile"]["onboarding_completed"])
        self.assertEqual({item["category"] for item in saved["interests"]}, {"NLP", "CV"})

        me_response = self.client.get("/api/v1/user/me")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(len(me_response.json()["interests"]), 2)
