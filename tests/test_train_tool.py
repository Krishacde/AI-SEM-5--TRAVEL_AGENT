import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import backend
from tools import train_tool


def api_response(trains, from_code="AAA", to_code="BBB", status=200):
    payload = {
        "success": True,
        "data": {
            "from": {"name": from_code, "code": from_code},
            "to": {"name": to_code, "code": to_code},
            "trains": trains,
        },
    }
    return SimpleNamespace(status_code=status, json=lambda: payload)


def train_payload(number, duration, train_type, distance=None):
    train = {
        "train": {
            "name": f"Train {number}",
            "number": number,
            "type": train_type,
            "runDays": ["MON", "WED"],
        },
        "from": {"departure": "08:00"},
        "to": {"arrival": "17:00"},
        "duration": duration,
    }
    if distance is not None:
        train["distance"] = distance
    return train


class TrainSearchTests(unittest.TestCase):
    @patch.object(train_tool, "RAILRADAR_API_KEY", "test-key")
    @patch.object(train_tool, "resolve_station_candidates")
    @patch.object(train_tool.requests, "get")
    @patch.object(train_tool, "_lookup_region_railheads")
    def test_merge_deduplicate_and_sort_by_numeric_duration(self, region_lookup, get, resolve):
        destination_candidates = [
            ("BBB", "Railhead 1"),
            ("BBB", "Railhead 1 alias"),
            ("CCC", "Railhead 2"),
        ]
        resolve.side_effect = [
            [("AAA", "Origin")],
            destination_candidates,
        ]
        region_lookup.return_value = destination_candidates
        train_tool._FETCH_CACHE.clear()
        get.side_effect = [
            api_response(
                [
                    train_payload("100", 605, "Express", 500),
                    train_payload("200", 570, "Express", 400),
                ],
                "AAA",
                "BBB",
            ),
            api_response(
                [train_payload("100", 600, "Express", 600)],
                "AAA",
                "CCC",
            ),
        ]

        result = train_tool.search_trains("Origin", "Himachal Pradesh", "2026-11-08", limit=5)

        self.assertEqual([train["duration_mins"] for train in result], [570, 600, 605])
        self.assertEqual(
            [(train["train_number"], train["arrival_station_code"]) for train in result],
            [("200", "BBB"), ("100", "CCC"), ("100", "BBB")],
        )
        self.assertEqual(get.call_count, 2)
        self.assertEqual(result[0]["duration"], "9h 30m")
        self.assertEqual(result[0]["rail_ends_note"], "Rail ends at BBB; continue by road to Himachal Pradesh")

    @patch.object(train_tool, "RAILRADAR_API_KEY", "test-key")
    @patch.object(train_tool.requests, "get")
    def test_price_estimates_and_missing_distance(self, get):
        get.return_value = api_response(
            [
                train_payload("1", 600, "Rajdhani", 1000),
                train_payload("2", 300, "Passenger", 200),
                train_payload("3", 240, "Express"),
            ]
        )
        result, error = train_tool._fetch_between("AAA", "BBB", "2026-11-08")

        self.assertIsNone(error)
        self.assertEqual(
            [(train["price_num"], train["price"], train["price_class"], train["price_status"]) for train in result],
            [
                (1300, "₹1,300", "3A", "ESTIMATED"),
                (80, "₹80", "2S", "ESTIMATED"),
                (None, None, "SL", "UNAVAILABLE"),
            ],
        )
        self.assertEqual(result[0]["run_days"], ["MON", "WED"])
        self.assertEqual(result[0]["distance_km"], 1000.0)
        self.assertIn("reservation fees and superfast surcharges", result[0]["price_note"])

    @patch.object(train_tool, "RAILRADAR_API_KEY", "test-key")
    @patch.object(train_tool, "resolve_station_candidates", return_value=[("AAA", "A")])
    @patch.object(train_tool.requests, "get")
    def test_failure_messages_are_distinct(self, get, _resolve):
        cases = [
            (SimpleNamespace(status_code=401, json=lambda: {}), "HTTP 401 Unauthorized"),
            (SimpleNamespace(status_code=403, json=lambda: {}), "HTTP 403 Forbidden"),
            (SimpleNamespace(status_code=429, json=lambda: {}), "rate limit reached"),
            (SimpleNamespace(status_code=503, json=lambda: {}), "upstream error (HTTP 503)"),
            (train_tool.requests.Timeout(), "request timed out"),
            (api_response([]), "returned no trains"),
        ]
        messages = []
        for mocked_response, expected_text in cases:
            with self.subTest(expected_text=expected_text):
                train_tool._FETCH_CACHE.clear()
                if isinstance(mocked_response, BaseException):
                    get.side_effect = mocked_response
                else:
                    get.side_effect = None
                    get.return_value = mocked_response
                message = train_tool.search_trains("Origin", "Destination", "2026-11-08")
                self.assertIsInstance(message, str)
                self.assertIn(expected_text, message)
                messages.append(message)
        self.assertEqual(len(messages), len(set(messages)))

    @patch.object(train_tool, "RAILRADAR_API_KEY", "test-key")
    @patch.object(train_tool, "resolve_station_candidates", side_effect=[
        [("AAA", "Origin")], [("BBB", "Destination")],
        [("AAA", "Origin")], [("BBB", "Destination")],
    ])
    @patch.object(train_tool.requests, "get")
    def test_no_service_finds_only_dates_with_trains(self, get, _resolve):
        train_tool._FETCH_CACHE.clear()
        base_date = date(2026, 11, 2)

        def response_for_date(_url, headers, params, timeout):
            travel_date = params["date"]
            if travel_date == "2026-11-03":
                trains = [train_payload("19667", 600, "Express")]
            elif travel_date == "2026-11-06":
                trains = [train_payload("16282", 610, "Express")]
            else:
                trains = []
            return api_response(trains)

        get.side_effect = response_for_date
        result = train_tool.search_trains("Origin", "Destination", base_date.isoformat())
        self.assertIsInstance(result, str)
        self.assertIn("returned no trains", result)

        alternates = train_tool.find_alternate_dates(
            "Origin", "Destination", base_date.isoformat()
        )
        self.assertEqual(
            alternates,
            [
                {
                    "date": "2026-11-03",
                    "weekday": "Tue",
                    "trains": [{
                        "number": "19667",
                        "name": "Train 19667",
                        "departure_time": "08:00",
                    }],
                },
                {
                    "date": "2026-11-06",
                    "weekday": "Fri",
                    "trains": [{
                        "number": "16282",
                        "name": "Train 16282",
                        "departure_time": "08:00",
                    }],
                },
            ],
        )
        self.assertEqual(get.call_count, 8)

    @patch.object(train_tool, "RAILRADAR_API_KEY", "test-key")
    @patch.object(train_tool, "resolve_station_candidates", return_value=[("AAA", "Station")])
    @patch.object(train_tool.requests, "get")
    @patch.object(backend, "find_alternate_dates")
    def test_train_node_does_not_probe_on_rate_limit(
        self, alternate_dates, get, _resolve
    ):
        train_tool._FETCH_CACHE.clear()
        get.return_value = SimpleNamespace(status_code=429, json=lambda: {})
        request = backend.TripRequest(
            origin="Vadodara",
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 3),
        )
        result = backend.trains({
            "request": request.model_dump(),
            "selected_destination": "Mysore",
            "execution_plan": {"include_trains": True},
        })
        alternate_dates.assert_not_called()
        get.assert_called_once()
        self.assertEqual(
            result["trains"][0]["message"],
            "RailRadar rate limit reached (HTTP 429).",
        )
        self.assertEqual(result["trains"][0]["alternate_dates"], [])

    @patch.object(train_tool, "RAILRADAR_API_KEY", None)
    def test_missing_key_message(self):
        self.assertIn(
            "RailRadar API key is missing",
            train_tool.search_trains("Origin", "Destination", "2026-11-08"),
        )


class PlannerDestinationTests(unittest.TestCase):
    @patch.object(backend, "resolve_station_candidates", return_value=[])
    @patch.object(backend.requests, "get")
    def test_unknown_indian_places_stay_open_and_tokyo_is_excluded(self, get, _resolve):
        request = backend.TripRequest(
            origin="Vadodara",
            start_date=date(2026, 11, 8),
            end_date=date(2026, 11, 10),
        )

        def geocode(_url, params, timeout):
            code = "JP" if params["name"] == "Tokyo" else "IN"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"results": [{"country_code": code}]},
            )

        get.side_effect = geocode
        plans = {
            place: backend.build_fallback_plan(request, place)
            for place in ("Kalpa", "Dwarka", "Tokyo, Japan")
        }

        self.assertTrue(plans["Kalpa"]["include_trains"])
        self.assertTrue(plans["Dwarka"]["include_trains"])
        self.assertFalse(plans["Tokyo, Japan"]["include_trains"])

        for place, plan in plans.items():
            overridden = backend._apply_safety_overrides(plan.copy(), request, place)
            self.assertEqual(overridden["include_trains"], plan["include_trains"])

    @patch.object(backend, "resolve_station_candidates", return_value=[])
    @patch.object(backend.requests, "get", side_effect=backend.requests.Timeout("offline"))
    def test_inconclusive_geocoding_fails_open(self, _get, _resolve):
        request = backend.TripRequest(
            origin="Vadodara",
            start_date=date(2026, 11, 8),
            end_date=date(2026, 11, 10),
        )
        plan = backend.build_fallback_plan(request, "Unresolved Place")
        overridden = backend._apply_safety_overrides(plan.copy(), request, "Unresolved Place")
        self.assertTrue(plan["include_trains"])
        self.assertTrue(overridden["include_trains"])


class NearestRailheadTests(unittest.TestCase):
    def setUp(self):
        train_tool._GEOCODE_CACHE.clear()
        train_tool._RAILHEAD_CACHE.clear()
        train_tool._RAILHEAD_DISTANCE_KM.clear()

    @patch.object(train_tool.requests, "get")
    def test_geocoded_places_choose_nearest_major_stations(self, get):
        coordinates = {
            "kalpa": (31.54, 78.26),
            "tiny test hamlet": (28.6448, 77.2167),
        }

        def geocode(_url, params, timeout):
            latitude, longitude = coordinates[params["name"]]
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "results": [{
                        "country_code": "IN",
                        "latitude": latitude,
                        "longitude": longitude,
                    }]
                },
            )

        get.side_effect = geocode
        self.assertEqual(
            train_tool.resolve_station_candidates("Kalpa"),
            [("SML", "Shimla"), ("DDN", "DEHRADUN"), ("KLK", "Kalka")],
        )
        self.assertEqual(
            train_tool.resolve_station_candidates("Tiny Test Hamlet"),
            [("NDLS", "NEW DELHI"), ("DLI", "OLD DELHI"), ("TKJ", "TILAK BRIDGE")],
        )
        self.assertEqual(
            train_tool.resolve_station_candidates("kalpa"),
            [("SML", "Shimla"), ("DDN", "DEHRADUN"), ("KLK", "Kalka")],
        )
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            train_tool._RAILHEAD_DISTANCE_KM["tiny test hamlet"][0],
            "NDLS",
        )

    @patch.object(train_tool.requests, "get")
    def test_foreign_place_returns_empty_and_dictionary_matches_are_unchanged(self, get):
        get.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "results": [{
                    "country_code": "US",
                    "latitude": 40.7,
                    "longitude": -74.0,
                }]
            },
        )
        self.assertEqual(train_tool.resolve_station_candidates("Unknownville"), [])
        self.assertEqual(
            train_tool.resolve_station_candidates("Coorg"),
            train_tool.REGION_TO_NEAREST_RAILHEAD["coorg"],
        )
        self.assertEqual(
            train_tool.resolve_station_candidates("Kainchi Dham"),
            train_tool.REGION_TO_NEAREST_RAILHEAD["kainchi dham"],
        )
        self.assertEqual(
            train_tool.resolve_station_candidates("Himachal Pradesh"),
            train_tool.REGION_TO_NEAREST_RAILHEAD["himachal pradesh"],
        )
        get.assert_called_once()

    @patch.object(train_tool.requests, "get", side_effect=train_tool.requests.Timeout("offline"))
    def test_unreachable_geocoder_fails_quietly(self, _get):
        self.assertEqual(train_tool.resolve_station_candidates("Remote Test Place"), [])
        self.assertEqual(train_tool.resolve_station_candidates("Remote Test Place"), [])


if __name__ == "__main__":
    unittest.main()
