import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from services.route_analyzer import _build_response


class RouteApiRegressionTests(unittest.TestCase):
    def test_route_comparison_does_not_add_two_step_to_violation_count(self):
        result = asyncio.run(_build_response(
            {"paths": [{"distance": 100.0}]},
            [{"rule": "two_step_turn"}],
            [],
            35.0, 139.0, 35.1, 139.1,
            using_edge_ids=True,
            algo_version="v3",
        ))
        self.assertEqual(result["comparison"]["violation_count"], 0)
        self.assertEqual(result["comparison"]["oneway_violation_count"], 0)
        self.assertEqual(result["comparison"]["two_step_required_intersections"], 1)
        self.assertFalse(result["rerouted"])

    def test_post_api_route_keeps_response_contract(self):
        expected = {
            "original_route": {"distance": 100.0},
            "compliant_route": {"distance": 100.0},
            "route": {"distance": 100.0},
            "violations": [],
            "compliant": True,
            "recommendations": [],
            "rerouted": False,
            "comparison": {"violation_count": 0, "rerouted": False},
        }
        with (
            patch("routers.route.analyze_route", new=AsyncMock(return_value=expected)) as analyze,
            patch("routers.route.get_guidance_for_route", new=AsyncMock(return_value=[])),
        ):
            with TestClient(app) as client:
                response = client.post("/api/route", json={
                    "origin_lat": 35.0,
                    "origin_lng": 139.0,
                    "dest_lat": 35.1,
                    "dest_lng": 139.1,
                })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {**expected, "guidance": []})
        analyze.assert_awaited_once_with(35.0, 139.0, 35.1, 139.1, algo_version="v3")

    def test_guidance_is_separate_and_does_not_change_violations(self):
        violations = [
            {"rule": "oneway", "node_id": 1},
            {"rule": "two_step_turn", "node_id": 2},
        ]
        analyzed = {
            "route": {"points": {"coordinates": []}},
            "violations": violations,
            "comparison": {"oneway_violation_count": 1,
                           "two_step_required_intersections": 1},
        }
        guidance = [{
            "type": "stop_sign", "node_id": 3,
            "lat": 35.0, "lng": 139.0, "way_id": 10,
            "distance_from_start_m": 50.0,
        }]
        with (
            patch("routers.route.analyze_route", new=AsyncMock(return_value=analyzed)),
            patch(
                "routers.route.get_guidance_for_route",
                new=AsyncMock(return_value=guidance),
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/api/route", json={
                    "origin_lat": 35.0, "origin_lng": 139.0,
                    "dest_lat": 35.1, "dest_lng": 139.1,
                })
        body = response.json()
        self.assertEqual(body["violations"], violations)
        self.assertEqual(body["guidance"], guidance)
        self.assertNotIn("rule", body["guidance"][0])

    def test_batch_csv_separates_oneway_and_two_step_counts(self):
        analyzed = {
            "violations": [
                {"rule": "oneway", "confidence": 1.0},
                {"rule": "two_step_turn", "confidence": 0.7},
            ],
            "comparison": {
                "algo_version": "v3",
                "original_distance_m": 100.0,
                "compliant_distance_m": 110.0,
                "distance_diff_m": 10.0,
                "distance_diff_pct": 10.0,
                "rerouted": True,
            },
        }
        with patch("routers.experiment.analyze_route", new=AsyncMock(return_value=analyzed)):
            with TestClient(app) as client:
                response = client.post("/api/experiment/batch/csv", json={
                    "routes": [{
                        "label": "sample",
                        "road_type": "residential",
                        "origin_lat": 35.0,
                        "origin_lng": 139.0,
                        "dest_lat": 35.1,
                        "dest_lng": 139.1,
                    }],
                    "algo_version": "v3",
                })
        lines = response.text.splitlines()
        self.assertEqual(response.status_code, 200)
        self.assertIn("oneway_violation_count", lines[0])
        self.assertIn("two_step_required_intersections", lines[0])
        self.assertNotIn("violation_count", lines[0].split(","))
        self.assertIn(",1,1,0,1,", lines[1])


if __name__ == "__main__":
    unittest.main()
