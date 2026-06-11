import os
import unittest

os.environ.setdefault("APP_MODE", "test")

from fastapi.testclient import TestClient

from static.dashboard.main import app


class DashboardSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_dashboard_page_loads(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_dashboard_api_works_without_csv_files(self):
        models = self.client.get("/api/models")
        sources = self.client.get("/api/sources")
        stats = self.client.get("/api/stats")
        export = self.client.get("/api/export_csv")

        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["items"], [])
        self.assertEqual(sources.status_code, 200)
        self.assertEqual(sources.json(), [])
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(export.status_code, 200)
