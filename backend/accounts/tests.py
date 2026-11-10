"""회원 가입·로그인·관심 팀/선수 API 테스트(WEB-11, WEB-12).

  docker compose exec -T django-api python manage.py test accounts

테스트용 DB에 make_league()로 작은 리그(팀·선수·경기)를 만든 뒤 API를 실제로 호출해 본다.
로그인 토큰은 "Authorization: Token <토큰>" 헤더로 보낸다.
"""
from django.core.cache import cache
from django.test import TestCase

from matches.tests import make_league

PASSWORD = "spike-and-block-77"  # 비밀번호 규칙(길이·흔한 비밀번호 금지 등)을 통과하는 테스트용 비밀번호


class AccountApiTest(TestCase):
    def setUp(self):
        cache.clear()  # 로그인 시도 횟수 제한(throttle) 카운트가 캐시에 있으므로 테스트마다 비운다

    def signup(self, email="Fan@Example.com", password=PASSWORD, nickname="배구팬"):
        """가입 요청을 보내고 응답을 돌려준다."""
        return self.client.post("/api/auth/signup", {"email": email, "password": password, "nickname": nickname},
                                content_type="application/json")

    def auth(self, token):
        """토큰 인증 헤더(테스트 클라이언트에 **로 넘길 dict)."""
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def test_signup_login_logout(self):
        """가입 → 로그인(이메일 대소문자 무시) → 내 정보 → 로그아웃 후 토큰 무효."""
        r = self.signup()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["user"]["email"], "fan@example.com")  # 소문자로 저장
        self.assertEqual(self.signup(email="fan@example.com").status_code, 400)  # 같은 이메일로 또 가입

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
        """숫자만 있거나 이메일과 너무 비슷한 비밀번호는 거절하고, 오류는 password 필드에 담는다."""
        r = self.signup(password="12345678")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.json())
        self.assertEqual(self.signup(password="fan@example.com1").status_code, 400)  # 이메일과 너무 비슷함

    def test_me_needs_login(self):
        """내 정보와 내 피드는 로그인해야 볼 수 있다(401)."""
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me/feed").status_code, 401)

    def test_favorites_and_feed(self):
        """관심 팀·선수 추가(중복은 한 번만), 없는 코드 404, 모르는 종류 400, 피드에 최근·다음 경기와 시즌 기록."""
        make_league()
        token = self.signup().json()["token"]
        h = self.auth(token)
        r = self.client.post("/api/me/favorites", {"kind": "team", "code": "1009"}, content_type="application/json", **h)
        self.assertEqual(r.status_code, 201)
        self.client.post("/api/me/favorites", {"kind": "team", "code": "1009"}, content_type="application/json", **h)
        self.client.post("/api/me/favorites", {"kind": "player", "code": "1000525"}, content_type="application/json", **h)
        fav = self.client.get("/api/me/favorites", **h).json()
        self.assertEqual([t["name"] for t in fav["teams"]], ["우리카드"])  # 두 번 추가해도 하나만
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
        """탈퇴는 비밀번호를 다시 확인해야 하고, 탈퇴 후에는 로그인할 수 없다(계정이 지워짐)."""
        token = self.signup().json()["token"]
        h = self.auth(token)
        self.assertEqual(self.client.delete("/api/me", {"password": "wrong"}, content_type="application/json", **h).status_code, 400)
        self.assertEqual(self.client.delete("/api/me", {"password": PASSWORD}, content_type="application/json", **h).status_code, 204)
        self.assertEqual(self.client.post("/api/auth/login", {"email": "fan@example.com", "password": PASSWORD},
                                          content_type="application/json").status_code, 400)

    def test_login_is_throttled(self):
        """로그인을 짧은 시간에 너무 많이 시도하면(기본 분당 10회) 429로 막는다(비밀번호 대입 방지)."""
        codes = [self.client.post("/api/auth/login", {"email": "x@example.com", "password": "x"},
                                  content_type="application/json").status_code for _ in range(12)]
        self.assertEqual(codes[-1], 429)
