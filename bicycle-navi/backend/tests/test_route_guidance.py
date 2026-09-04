import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services.overpass import GUIDANCE_SNAPSHOT_DATE, get_route_guidance_data
from services.route_guidance import build_route_guidance


def way(way_id, node_ids, coordinates):
    return {"nodes": node_ids, "geometry": coordinates}


class RouteGuidanceTests(unittest.TestCase):
    def test_detects_only_supported_node_tags(self):
        points = [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]
        details = [[0, 4, 10]]
        ways = {10: way(10, [1, 2, 3, 4, 5], points)}
        tagged = {
            2: {"tags": {"highway": "stop"}},
            3: {"tags": {"railway": "level_crossing"}},
            4: {"tags": {"railway": "crossing", "highway": "give_way"}},
        }
        result = build_route_guidance(points, details, ways, tagged)
        self.assertEqual([item["type"] for item in result], ["stop_sign", "level_crossing"])
        self.assertTrue(all("rule" not in item for item in result))

    def test_nodes_outside_traversed_section_are_not_detected(self):
        all_coords = [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]
        points = all_coords[1:4]
        result = build_route_guidance(
            points,
            [[0, 2, 10]],
            {10: way(10, [1, 2, 3, 4, 5], all_coords)},
            {
                1: {"tags": {"highway": "stop"}},
                3: {"tags": {"highway": "stop"}},
                5: {"tags": {"railway": "level_crossing"}},
            },
        )
        self.assertEqual([item["node_id"] for item in result], [3])

    def test_closed_loop_selects_only_traversed_arc(self):
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        # 始点から対角まで右・上の弧を走行。反対側の node 13 は対象外。
        route = [[0, 0], [1, 0], [1, 1]]
        result = build_route_guidance(
            route,
            [[0, 2, 99]],
            {99: way(99, [10, 11, 12, 13, 10], ring)},
            {
                11: {"tags": {"highway": "stop"}},
                13: {"tags": {"railway": "level_crossing"}},
            },
        )
        self.assertEqual([item["node_id"] for item in result], [11])

    def test_same_node_on_two_laps_is_kept_twice_by_distance(self):
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        points = ring + ring[1:]
        result = build_route_guidance(
            points,
            [[0, 4, 99], [4, 8, 99]],
            {99: way(99, [10, 11, 12, 13, 10], ring)},
            {11: {"tags": {"highway": "stop"}}},
        )
        self.assertEqual([item["node_id"] for item in result], [11, 11])
        self.assertLess(result[0]["distance_from_start_m"], result[1]["distance_from_start_m"])

    def test_overpass_query_is_one_fixed_attic_bulk_request(self):
        elements = [
            {"type": "way", "id": 10, "nodes": [1, 2],
             "geometry": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}]},
            {"type": "node", "id": 2, "lon": 1, "lat": 0,
             "tags": {"highway": "stop"}},
        ]
        with patch("services.overpass._post_with_retry", new=AsyncMock(return_value=elements)) as post:
            result = asyncio.run(get_route_guidance_data([10, 20, 10]))
        post.assert_awaited_once()
        query = post.await_args.args[0]
        self.assertIn(f'[date:"{GUIDANCE_SNAPSHOT_DATE}"]', query)
        self.assertIn("way(id:10,20)", query)
        self.assertNotIn("give_way", query)
        self.assertEqual(result["ways"][10]["nodes"], [1, 2])


if __name__ == "__main__":
    unittest.main()
