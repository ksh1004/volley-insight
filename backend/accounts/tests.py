"""Sign-up, login and favourites (WEB-11, WEB-12)."""
from django.core.cache import cache
from django.test import TestCase

from matches.tests import make_league

PASSWORD = "spike-and-block-77"


class AccountApiTest(TestCase):
    def setUp(self):
        cache.clear()  # login throttle counts live in the cache

    def signup(self, email="Fan@Example.com", password=PASSWORD, nickname="배구팬"):
        return self.client.post("/api/auth/signup", {"email": email, "password": password, "nickname": nickname},
                                content_type="application/json")

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def test_signup_login_logout(self):
        r = self.signup()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["user"]["email"], "fan@example.com")  # stored lower case
        self.assertEqual(self.signup(email="fan@example.com").status_code, 400)  # same email again

        bad = self.client.post("/api/auth/login", {"email": "fan@example.com", "password": "wrong"},
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        r = self.client.post("/api/auth/login", {"email": "FAN@example.com", "password": PASSWORD},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        token = r.json()["token"]
        self.assertEqual(self.client.get("/api/me", **self.auth(token)).json()["user"]["nickname"], "배구팬")

        self.assertEqual(self.client.post("/api/auth/logout", **self.auth(token)).status_code, 204)
        self.assertEqual(self.client.get("/api/me", **self.auth(token)).status_code, 401)

    def test_weak_password_is_rejected(self):
        r = self.signup(password="12345678")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.json())
        self.assertEqual(self.signup(password="fan@example.com1").status_code, 400)  # too close to the email

    def test_me_needs_login(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me/feed").status_code, 401)

    def test_favorites_and_feed(self):
        make_league()
        token = self.signup().json()["token"]
        h = self.auth(token)
        r = self.client.post("/api/me/favorites", {"kind": "team", "code": "1009"}, content_type="application/json", **h)
        self.assertEqual(r.status_code, 201)
        self.client.post("/api/me/favorites", {"kind": "team", "code": "1009"}, content_type="application/json", **h)
        self.client.post("/api/me/favorites", {"kind": "player", "code": "1000525"}, content_type="application/json", **h)
        fav = self.client.get("/api/me/favorites", **h).json()
        self.assertEqual([t["name"] for t in fav["teams"]], ["우리카드"])  # added twice, kept once
        self.assertEqual([p["name"] for p in fav["players"]], ["아라우조"])
        self.assertEqual(self.client.post("/api/me/favorites", {"kind": "team", "code": "none"},
                                          content_type="application/json", **h).status_code, 404)
        self.assertEqual(self.client.post("/api/me/favorites", {"kind": "coach", "code": "1"},
                                          content_type="application/json", **h).status_code, 400)

        feed = self.client.get("/api/me/feed", **h).json()
        team = feed["teams"][0]
        self.assertEqual(team["record"]["wins"], 1)
        self.assertEqual(team["last"]["key"], "022-3")
        self.assertEqual(team["next"]["key"], "022-10")
        player = feed["players"][0]
        self.assertEqual(player["last"]["points"], 23)
        self.assertEqual(player["last"]["result"], "W")
        self.assertEqual(player["season"]["games"], 1)

        r = self.client.delete("/api/me/favorites/team/1009", **h)
        self.assertEqual(r.json()["teams"], [])

    def test_delete_account(self):
        token = self.signup().json()["token"]
        h = self.auth(token)
        self.assertEqual(self.client.delete("/api/me", {"password": "wrong"}, content_type="application/json", **h).status_code, 400)
        self.assertEqual(self.client.delete("/api/me", {"password": PASSWORD}, content_type="application/json", **h).status_code, 204)
        self.assertEqual(self.client.post("/api/auth/login", {"email": "fan@example.com", "password": PASSWORD},
                                          content_type="application/json").status_code, 400)

    def test_login_is_throttled(self):
        codes = [self.client.post("/api/auth/login", {"email": "x@example.com", "password": "x"},
                                  content_type="application/json").status_code for _ in range(12)]
        self.assertEqual(codes[-1], 429)
