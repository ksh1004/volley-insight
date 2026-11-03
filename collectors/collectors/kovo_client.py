"""Client for the public JSON API that kovo.co.kr itself uses (requirements 4.1.1).

Every request goes through one RateLimiter, identifies the project honestly in the User-Agent,
and never logs in or bypasses any access control (NFR-01, NFR-04).
"""
import logging
import time

import requests

from . import settings
from .rate_limiter import default_limiter

log = logging.getLogger(__name__)

USER_AGENT = "volley-insight-collector/0.1 (non-commercial student project)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class KovoApiError(RuntimeError):
    pass


class KovoClient:
    def __init__(self, base_url=None, limiter=None, session=None, max_retries=3, backoff_sec=2.0, timeout=10):
        self.base_url = (base_url or settings.KOVO_API_BASE).rstrip("/")
        self.limiter = limiter or default_limiter(settings.KOVO_MIN_INTERVAL_SEC, settings.KOVO_RATE_LOCK_FILE)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "ko"})
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self.timeout = timeout

    # ---------- low level ----------
    def get(self, path: str, params: dict | None = None) -> dict:
        """GET a KOVO endpoint and return the parsed body. Retries on 429/5xx and network errors."""
        if not path.startswith("/"):  # e.g. Git Bash turned "/stat/..." into "C:/Program Files/Git/stat/..."
            raise ValueError(f"KOVO API path must start with '/': {path!r}")
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise KovoApiError(f"GET {path} failed: {exc}") from exc
                self._backoff(attempt, path, str(exc))
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                self._backoff(attempt, path, f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise KovoApiError(f"GET {path} returned HTTP {resp.status_code}")
            body = resp.json()
            status = (body.get("result") or {}).get("status", 200) if isinstance(body, dict) else 200
            if status != 200:
                raise KovoApiError(f"GET {path} returned result.status={status}: {body.get('result')}")
            return body
        raise KovoApiError(f"GET {path} gave up after {self.max_retries} retries")

    def _backoff(self, attempt, path, reason):
        delay = self.backoff_sec * (2 ** attempt)
        log.warning("retrying %s in %.0fs (%s)", path, delay, reason)
        time.sleep(delay)

    # ---------- endpoints ----------
    def season_list(self, gcode: str = "001", season_code: str | None = None) -> dict:
        """Seasons and their leagues. gcode 001 = V-League, 002 = KOVO Cup."""
        return self.get("/stat/season-list", {"gcode": gcode, "seasonCode": season_code})

    def game_schedule(self, gcode: str, season_code: str, league_code: str, gender: str,
                      page: int = 0, size: int = 200) -> dict:
        """Game list for one season/league/gender. gender: '1' men, '2' women."""
        return self.get("/stat/game-schedule", {
            "gcode": gcode, "seasonCode": season_code, "leagueCode": league_code,
            "gender": gender, "page": page, "size": size,
        })

    def game_detail(self, gcode: str, season_code: str, league_code: str, gnum) -> dict:
        """One game: meta, per-player and per-team box score, category leaders."""
        return self.get(f"/stat/game-schedule/{gnum}",
                        {"gcode": gcode, "seasonCode": season_code, "leagueCode": league_code})

    def real_time(self, gcode: str, season_code: str, gnum, hcode: str, acode: str, rset: int,
                  page: int = 0, size: int = 500) -> dict:
        """Play-by-play events of one set (`payload.feed`), plus the set's starting line-up."""
        return self.get("/stat/real-time", {
            "season": season_code, "gcode": gcode, "gnum": gnum, "hcode": hcode, "acode": acode,
            "rset": rset, "page": page, "size": size,
        })
