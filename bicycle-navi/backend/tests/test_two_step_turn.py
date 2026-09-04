import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services.law_checker import check_two_step_turn
from services.overpass import (
    INTERSECTION_EXCLUDED_HIGHWAYS,
    _way_edge_contribution,
    build_intersection_data,
)
from services.route_analyzer import _extract_right_turns, _way_ids_at_turn
from services.external_route_scorer import (
    _extract_right_turn_contexts,
    _segment_midpoint,
    _side_reference_indices,
    score_external_route,
)


def way(way_id, highway, nodes, coordinates):
    return {
        "type": "way",
        "id": way_id,
        "tags": {"highway": highway},
        "nodes": nodes,
        "geometry": [
            {"lon": lng, "lat": lat} for lng, lat in coordinates
        ],
    }


class EdgeCountTests(unittest.TestCase):
    def test_endpoint_and_middle_contributions(self):
        self.assertEqual(_way_edge_contribution(10, [10, 11, 12]), 1)
        self.assertEqual(_way_edge_contribution(11, [10, 11, 12]), 2)
        self.assertEqual(_way_edge_contribution(99, [10, 11, 12]), 0)

    def test_split_single_road_is_two_edges_not_intersection(self):
        elements = [
            way(1, "residential", [10, 20], [[139.0, 35.0], [139.001, 35.0]]),
            way(2, "residential", [20, 30], [[139.001, 35.0], [139.002, 35.0]]),
        ]
        data = build_intersection_data(
            [[139.001, 35.0]], elements, entry_way_ids=[1], exit_way_ids=[2],
        )[0]
        self.assertEqual(data["edge_count"], 2)
        self.assertFalse(data["is_intersection"])

    def test_t_junction_has_three_edges(self):
        elements = [
            way(1, "residential", [10, 20, 30], [[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]]),
            way(2, "residential", [20, 40], [[139.001, 35.0], [139.001, 35.001]]),
        ]
        data = build_intersection_data(
            [[139.001, 35.0]], elements, entry_way_ids=[1], exit_way_ids=[2],
        )[0]
        self.assertEqual(data["edge_count"], 3)
        self.assertTrue(data["is_intersection"])

    def test_crossroads_has_four_edges(self):
        elements = [
            way(1, "residential", [10, 20, 30], [[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]]),
            way(2, "unclassified", [40, 20, 50], [[139.001, 34.999], [139.001, 35.0], [139.001, 35.001]]),
        ]
        data = build_intersection_data(
            [[139.001, 35.0]], elements, entry_way_ids=[1], exit_way_ids=[2],
        )[0]
        self.assertEqual(data["edge_count"], 4)
        self.assertTrue(data["is_intersection"])

    def test_excluded_way_is_reported_but_not_counted(self):
        elements = [
            way(1, "residential", [10, 20, 30], [[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]]),
            way(2, "service", [20, 40], [[139.001, 35.0], [139.001, 35.001]]),
            way(3, "cycleway", [20, 50], [[139.001, 35.0], [139.001, 34.999]]),
        ]
        data = build_intersection_data(
            [[139.001, 35.0]], elements, entry_way_ids=[1], exit_way_ids=[3],
        )[0]
        self.assertEqual(data["edge_count"], 3)
        service = next(w for w in data["connected_ways"] if w["way_id"] == 2)
        cycleway = next(w for w in data["connected_ways"] if w["way_id"] == 3)
        self.assertTrue(service["excluded"])
        self.assertFalse(cycleway["excluded"])
        self.assertNotIn("cycleway", INTERSECTION_EXCLUDED_HIGHWAYS)


class TwoStepDecisionTests(unittest.TestCase):
    def test_residential_t_junction_is_detected(self):
        intersection = {
            "node_id": 20,
            "edge_count": 3,
            "connected_ways": [],
            "entry_way_id": 1,
            "exit_way_id": 2,
            "entry_or_exit_excluded": False,
            "is_intersection": True,
        }
        violations = asyncio.run(check_two_step_turn(
            [[139.001, 35.0]], intersection_data=[intersection],
        ))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["rule"], "two_step_turn")
        self.assertEqual(violations[0]["edge_count"], 3)

    def test_parking_or_private_entry_is_suppressed(self):
        elements = [
            way(1, "residential", [10, 20, 30], [[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]]),
            way(2, "service", [20, 40], [[139.001, 35.0], [139.001, 35.001]]),
            way(3, "residential", [20, 50], [[139.001, 35.0], [139.001, 34.999]]),
        ]
        intersection = build_intersection_data(
            [[139.001, 35.0]], elements, entry_way_ids=[1], exit_way_ids=[2],
        )[0]
        self.assertEqual(intersection["edge_count"], 3)
        self.assertTrue(intersection["entry_or_exit_excluded"])
        violations = asyncio.run(check_two_step_turn(
            [[139.001, 35.0]], intersection_data=[intersection],
        ))
        self.assertEqual(violations, [])

    def test_external_score_exposes_separate_counts_without_total(self):
        score = asyncio.run(score_external_route([]))
        self.assertEqual(score["oneway_violation_count"], 0)
        self.assertEqual(score["two_step_required_intersections"], 0)
        self.assertNotIn("two_step_violation_count", score)
        self.assertNotIn("total_violation_count", score)


class RightTurnExtractionTests(unittest.TestCase):
    def test_side_reference_indices_reuse_thirty_meter_span(self):
        # 緯度方向約11.1m間隔。中央から前後とも3点進んで30mを超える。
        coords = [[139.0, 35.0 + i * 0.0001] for i in range(7)]
        self.assertEqual(_side_reference_indices(coords, 3), (0, 6))

    def test_turn_way_references_use_adjacent_segments(self):
        coords = [[0.0, 0.0], [0.001, 0.0], [0.001, -0.001]]
        turns = _extract_right_turn_contexts(coords, [0, 1, 2])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["entry_ref_idx"], 0)
        self.assertEqual(turns[0]["exit_ref_idx"], 2)
        self.assertEqual(_segment_midpoint(coords[0], coords[1]), [0.0005, 0.0])

    def test_only_graphhopper_right_turn_signs_are_extracted(self):
        points = [[0, 0], [1, 0], [1, -1], [0, -1]]
        route_data = {"paths": [{"instructions": [
            {"sign": 0, "interval": [0, 1]},
            {"sign": 2, "interval": [1, 2]},
            {"sign": -2, "interval": [2, 3]},
            {"sign": 3, "interval": [3, 3]},
        ]}]}
        turn_points, turn_indices = _extract_right_turns(route_data, points)
        self.assertEqual(turn_indices, [1, 3])
        self.assertEqual(turn_points, [points[1], points[3]])

    def test_entry_and_exit_way_ids_use_detail_boundary(self):
        details = [[0, 4, 100], [4, 8, 200]]
        self.assertEqual(_way_ids_at_turn(details, 4), (100, 200))


class ExternalRouteWayInferenceTests(unittest.TestCase):
    @staticmethod
    def _match(candidates):
        first = candidates[0]
        return {
            "tags": first["tags"],
            "geometry": [],
            "match_way_id": first["way_id"],
            "match_dist_m": first["distance_m"],
            "match_margin_m": (
                candidates[1]["distance_m"] - first["distance_m"]
                if len(candidates) > 1 else None
            ),
            "match_ambiguous": len(candidates) > 1,
            "match_candidates": candidates,
        }

    def _score_one_turn(self, entry_candidates, exit_candidates):
        coords = [
            [139.0000, 35.0000],
            [139.0000, 35.0004],
            [139.0004, 35.0004],
            [139.0008, 35.0004],
        ]
        sample_matches = [
            {"tags": {}, "geometry": [], "match_way_id": 1,
             "match_dist_m": 0.0, "match_margin_m": 5.0, "match_ambiguous": False}
            for _ in coords
        ]
        side_matches = [
            self._match(entry_candidates),
            self._match(exit_candidates),
        ]
        intersection = {
            "node_id": 99,
            "edge_count": 3,
            "connected_ways": [],
            "entry_way_id": entry_candidates[0]["way_id"],
            "exit_way_id": exit_candidates[0]["way_id"],
            "entry_or_exit_excluded": False,
            "is_intersection": True,
        }
        with (
            patch(
                "services.external_route_scorer.get_bulk_way_data",
                new=AsyncMock(side_effect=[sample_matches, side_matches]),
            ),
            patch(
                "services.external_route_scorer.get_bulk_intersection_data",
                new=AsyncMock(return_value=[intersection]),
            ),
            patch(
                "services.external_route_scorer.check_oneway_violation",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "services.external_route_scorer._extract_right_turn_contexts",
                return_value=[{
                    "point": coords[1], "route_idx": 1,
                    "entry_ref_idx": 0, "exit_ref_idx": 2, "angle_deg": 90.0,
                }],
            ),
        ):
            score = asyncio.run(score_external_route(coords, sample_interval_m=1.0))
        return score

    @staticmethod
    def _candidate(way_id, highway, distance_m):
        return {
            "way_id": way_id,
            "highway": highway,
            "distance_m": distance_m,
            "tags": {"highway": highway},
        }

    def test_all_excluded_candidates_are_ambiguous_but_determinate(self):
        score = self._score_one_turn(
            [self._candidate(10, "service", 0.1), self._candidate(11, "footway", 1.0)],
            [self._candidate(20, "pedestrian", 0.1), self._candidate(21, "path", 0.8)],
        )
        diagnostic = score["two_step_diagnostics"][0]
        self.assertEqual(diagnostic["status"], "excluded")
        self.assertTrue(diagnostic["ambiguous"])
        self.assertTrue(diagnostic["determinate"])
        self.assertTrue(diagnostic["ambiguous_but_determinate"])
        self.assertEqual(score["two_step_ambiguous_determinate_count"], 1)
        self.assertEqual(score["two_step_unknown_count"], 0)

    def test_split_ways_with_same_highway_are_ambiguous_but_determinate(self):
        score = self._score_one_turn(
            [self._candidate(10, "residential", 0.1), self._candidate(11, "residential", 0.7)],
            [self._candidate(20, "residential", 0.1)],
        )
        diagnostic = score["two_step_diagnostics"][0]
        self.assertEqual(diagnostic["status"], "detected")
        self.assertTrue(diagnostic["ambiguous_but_determinate"])
        self.assertEqual(score["two_step_required_intersections"], 1)

    def test_excluded_and_nonexcluded_candidates_make_decision_unknown(self):
        score = self._score_one_turn(
            [self._candidate(10, "service", 0.1), self._candidate(11, "residential", 0.8)],
            [self._candidate(20, "residential", 0.1)],
        )
        diagnostic = score["two_step_diagnostics"][0]
        self.assertEqual(diagnostic["status"], "unknown")
        self.assertFalse(diagnostic["determinate"])
        self.assertEqual(
            {result["decision"] for result in diagnostic["combination_results"]},
            {"detected", "excluded"},
        )
        self.assertEqual(score["two_step_unknown_count"], 1)
        self.assertEqual(score["two_step_required_intersections"], 0)

    def test_single_candidates_are_unambiguous_and_determinate(self):
        score = self._score_one_turn(
            [self._candidate(10, "residential", 0.1)],
            [self._candidate(20, "residential", 0.1)],
        )
        diagnostic = score["two_step_diagnostics"][0]
        self.assertEqual(diagnostic["status"], "detected")
        self.assertFalse(diagnostic["ambiguous"])
        self.assertTrue(diagnostic["determinate"])
        self.assertFalse(diagnostic["ambiguous_but_determinate"])
        self.assertEqual(score["two_step_unambiguous_determinate_count"], 1)

if __name__ == "__main__":
    unittest.main()
