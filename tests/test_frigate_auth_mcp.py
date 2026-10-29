import json
import os
import time
import tomllib
import unittest
from datetime import datetime, timezone
from unittest import mock

import httpx

from frigate_auth_mcp import server

EVENT = {
    "id": "1761000000.123-abc", "camera": "terrace", "label": "cat", "sub_label": None,
    "start_time": 1761000000.0, "end_time": 1761000060.0, "zones": ["deck"],
    "has_clip": True, "has_snapshot": True, "data": {"top_score": 0.91, "description": "A cat on the deck."},
}
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeFrigate:
    """Minimal Frigate API on an httpx.MockTransport; records every request."""

    def __init__(self, token_valid=True):
        self.requests = []
        self.token_valid = token_valid

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/api/login":
            self.token_valid = True
            return httpx.Response(200, json={}, headers={"set-cookie": "frigate_token=jwt; Path=/"})
        if not self.token_valid:
            return httpx.Response(401, json={"message": "unauthorized"})
        if path == "/api/events":
            return httpx.Response(200, json=[EVENT, {**EVENT, "id": "2", "data": {}}])
        if path.startswith("/api/events/") and path.endswith("/snapshot.jpg"):
            return httpx.Response(200, content=JPEG)
        if path.startswith("/api/events/"):
            return httpx.Response(200, json=EVENT)
        if path.endswith("/latest.jpg"):
            return httpx.Response(200, content=JPEG)
        if path == "/api/review":
            return httpx.Response(200, json=[{"id": "r1", "camera": "terrace", "severity": "alert",
                                              "start_time": 1761000000.0, "end_time": None,
                                              "data": {"objects": ["cat"]}, "has_been_reviewed": False}])
        if path == "/api/review/summary":
            days = {f"2026-10-{d:02d}": {"total_alert": d} for d in range(1, 21)}
            return httpx.Response(200, json={"last24Hours": {"total_alert": 3}, **days})
        if path == "/api/stats":
            return httpx.Response(200, json={"service": {"uptime": 42},
                                             "cameras": {"terrace": {"camera_fps": 5.0, "detection_fps": 1.0}}})
        if path == "/api/profile":
            return httpx.Response(200, json={"username": "assistant", "role": "viewer",
                                             "allowed_cameras": ["terrace"]})
        if path == "/api/version":
            return httpx.Response(200, text="0.17.0")
        return httpx.Response(404)

    def params(self, path):
        return [dict(r.url.params) for r in self.requests if r.url.path == path]


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.frigate = FakeFrigate()
        client = httpx.Client(base_url="http://frigate.test:8971", transport=httpx.MockTransport(self.frigate))
        patches = [
            mock.patch.object(server, "_client", client),
            mock.patch.object(server, "IMAGE_CAMERAS", set()),
            mock.patch.dict(os.environ, {"FRIGATE_USER": "assistant", "FRIGATE_PASSWD": "secret"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TimestampTest(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(server._ts(None))

    def test_unix_seconds(self):
        self.assertEqual(server._ts("1761000000"), 1761000000.0)

    def test_iso_with_offset(self):
        self.assertEqual(server._ts("2026-10-20T12:00:00+00:00"),
                         datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp())

    def test_naive_iso_is_local_time(self):
        self.assertEqual(server._ts("2026-10-20T12:00:00"), datetime(2026, 10, 20, 12).timestamp())

    def test_iso_output_empty_for_missing_time(self):
        self.assertIsNone(server._iso(None))


class AuthTest(ServerTestCase):
    def test_login_on_401_then_retry(self):
        self.frigate.token_valid = False
        self.assertEqual(server.get_event("x")["id"], EVENT["id"])
        paths = [r.url.path for r in self.frigate.requests]
        self.assertEqual(paths, ["/api/events/x", "/api/login", "/api/events/x"])
        login = next(r for r in self.frigate.requests if r.url.path == "/api/login")
        self.assertEqual(json.loads(login.content), {"user": "assistant", "password": "secret"})

    def test_missing_credentials_clear_error(self):
        self.frigate.token_valid = False
        with mock.patch.dict(os.environ, {"FRIGATE_USER": ""}):
            with self.assertRaisesRegex(RuntimeError, "FRIGATE_USER and FRIGATE_PASSWD must be set"):
                server.get_event("x")

    def test_only_get_besides_login(self):
        server.frigate_status()
        server.list_events()
        server.get_event("x")
        server.event_snapshot("x")
        server.latest_frame("terrace")
        server.list_reviews()
        server.review_summary()
        writes = [r for r in self.frigate.requests if r.method != "GET" and r.url.path != "/api/login"]
        self.assertEqual(writes, [])


class EventsTest(ServerTestCase):
    def test_event_fields(self):
        event = server.get_event("x")
        self.assertEqual(event["camera"], "terrace")
        self.assertEqual(event["score"], 0.91)
        self.assertEqual(event["description"], "A cat on the deck.")

    def test_has_description_over_fetches_and_trims(self):
        events = server.list_events(limit=1, has_description=True)
        self.assertEqual([e["id"] for e in events], [EVENT["id"]])
        self.assertEqual(self.frigate.params("/api/events")[0]["limit"], "10")

    def test_unset_filters_are_not_sent(self):
        server.list_events()
        params = self.frigate.params("/api/events")[0]
        self.assertNotIn("cameras", params)
        self.assertNotIn("after", params)


class ImagesTest(ServerTestCase):
    def test_frame_returned(self):
        self.assertEqual(server.latest_frame("terrace").data, JPEG)

    def test_filter_refuses_other_cameras(self):
        with mock.patch.object(server, "IMAGE_CAMERAS", {"street"}):
            with self.assertRaises(ValueError):
                server.latest_frame("terrace")
            with self.assertRaises(ValueError):
                server.event_snapshot("x")
        self.assertEqual(self.frigate.params("/api/terrace/latest.jpg"), [])

    def test_filter_allows_listed_camera(self):
        with mock.patch.object(server, "IMAGE_CAMERAS", {"terrace"}):
            self.assertEqual(server.event_snapshot("x").data, JPEG)

    def test_filter_does_not_touch_text_tools(self):
        with mock.patch.object(server, "IMAGE_CAMERAS", {"street"}):
            self.assertEqual(server.get_event("x")["camera"], "terrace")


class ReviewsTest(ServerTestCase):
    def test_after_defaults_to_seven_days(self):
        server.list_reviews()
        after = float(self.frigate.params("/api/review")[0]["after"])
        self.assertAlmostEqual(after, time.time() - 7 * 86400, delta=60)

    def test_explicit_after_is_kept(self):
        server.list_reviews(after="1761000000")
        self.assertEqual(float(self.frigate.params("/api/review")[0]["after"]), 1761000000.0)

    def test_summary_keeps_last24_and_newest_days(self):
        summary = server.review_summary(days=3)
        self.assertEqual(list(summary), ["last24Hours", "2026-10-20", "2026-10-19", "2026-10-18"])


class StatusTest(ServerTestCase):
    def test_status(self):
        status = server.frigate_status()
        self.assertEqual(status["version"], "0.17.0")
        self.assertEqual(status["role"], "viewer")
        self.assertEqual(status["allowed_cameras"], ["terrace"])
        self.assertEqual(status["cameras"]["terrace"]["camera_fps"], 5.0)



class ReleaseMetadataTest(unittest.TestCase):
    def test_versions_match(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pyproject.toml"), "rb") as f:
            version = tomllib.load(f)["project"]["version"]
        with open(os.path.join(root, "server.json")) as f:
            server = json.load(f)
        self.assertEqual(server["version"], version)
        self.assertEqual([p["version"] for p in server["packages"]], [version])

    def test_readme_names_the_registry_server(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "server.json")) as f:
            name = json.load(f)["name"]
        with open(os.path.join(root, "README.md")) as f:
            self.assertIn(f"<!-- mcp-name: {name} -->", f.read())

if __name__ == "__main__":
    unittest.main()
