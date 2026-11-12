"""kovo.co.kr 사이트가 스스로 쓰는 공개 JSON API를 호출하는 클라이언트(요구사항 4.1.1).

지키는 원칙(NFR-01, NFR-04)
  - 모든 요청은 하나의 RateLimiter를 거친다(1초에 1번 이하).
  - User-Agent에 이 프로젝트를 그대로 밝힌다(브라우저인 척하지 않음).
  - 로그인하거나 접근 제한을 우회하지 않는다.
일시적인 오류(429 너무 많은 요청, 5xx 서버 오류, 네트워크 오류)는 간격을 늘려 가며 다시 시도한다.
"""
import logging
import time

import requests

from . import settings
from .rate_limiter import default_limiter

log = logging.getLogger(__name__)

USER_AGENT = "volley-insight-collector/0.1 (non-commercial student project)"
RETRY_STATUS = {429, 500, 502, 503, 504}  # 다시 시도할 HTTP 상태 코드


class KovoApiError(RuntimeError):
    """KOVO API가 실패 응답을 주거나, 재시도를 다 써도 성공하지 못했을 때 던지는 예외.
    status: 실패 응답의 HTTP 상태나 본문의 result.status(예: 404 = 그런 대상 없음). 네트워크 실패면 None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class KovoClient:
    """KOVO API 호출기. 아래 '엔드포인트' 메서드들이 실제로 쓰는 API 목록이다."""

    def __init__(self, base_url=None, limiter=None, session=None, max_retries=3, backoff_sec=2.0, timeout=10):
        """limiter/session은 테스트에서 가짜로 바꿔 끼울 수 있다. backoff_sec: 첫 재시도 대기(초), 매번 2배로 늘어남."""
        self.base_url = (base_url or settings.KOVO_API_BASE).rstrip("/")
        self.limiter = limiter or default_limiter(settings.KOVO_MIN_INTERVAL_SEC, settings.KOVO_RATE_LOCK_FILE)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "ko"})
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self.timeout = timeout

    # ---------- 저수준 호출 ----------
    def get(self, path: str, params: dict | None = None) -> dict:
        """KOVO API를 GET으로 부르고 JSON 본문을 돌려준다. 429/5xx와 네트워크 오류는 재시도한다.

        HTTP 200이어도 본문의 result.status가 200이 아니면 실패로 본다(KOVO API의 오류 표시 방식).
        값이 None인 파라미터는 빼고 보낸다.
        """
        if not path.startswith("/"):  # 예: Git Bash가 "/stat/..."를 "C:/Program Files/Git/stat/..."로 바꿔 버린 경우
            raise ValueError(f"KOVO API path must start with '/': {path!r}")
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()  # 재시도를 포함해 모든 요청이 간격 제한을 지킨다
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
                raise KovoApiError(f"GET {path} returned HTTP {resp.status_code}", status=resp.status_code)
            body = resp.json()
            status = (body.get("result") or {}).get("status", 200) if isinstance(body, dict) else 200
            if status != 200:
                raise KovoApiError(f"GET {path} returned result.status={status}: {body.get('result')}", status=status)
            return body
        raise KovoApiError(f"GET {path} gave up after {self.max_retries} retries")

    def _backoff(self, attempt, path, reason):
        """재시도 전 대기. 2초, 4초, 8초처럼 시도할 때마다 두 배로 늘린다(지수 백오프)."""
        delay = self.backoff_sec * (2 ** attempt)
        log.warning("retrying %s in %.0fs (%s)", path, delay, reason)
        time.sleep(delay)

    # ---------- 엔드포인트 ----------
    def season_list(self, gcode: str = "001", season_code: str | None = None) -> dict:
        """시즌 목록과 시즌별 리그 구성. gcode 001 = V-리그, 002 = KOVO컵."""
        return self.get("/stat/season-list", {"gcode": gcode, "seasonCode": season_code})

    def game_schedule(self, gcode: str, season_code: str, league_code: str, gender: str,
                      page: int = 0, size: int = 200) -> dict:
        """한 시즌·리그·성별의 경기 일정과 결과 목록. gender: '1' 남자부, '2' 여자부.
        league_code 예: 201 정규리그(그 밖의 코드는 포스트시즌 등)."""
        return self.get("/stat/game-schedule", {
            "gcode": gcode, "seasonCode": season_code, "leagueCode": league_code,
            "gender": gender, "page": page, "size": size,
        })

    def game_detail(self, gcode: str, season_code: str, league_code: str, gnum) -> dict:
        """경기 하나의 상세: 경기 정보, 선수별·팀별 기록(박스스코어), 부문별 1위."""
        return self.get(f"/stat/game-schedule/{gnum}",
                        {"gcode": gcode, "seasonCode": season_code, "leagueCode": league_code})

    def real_time(self, gcode: str, season_code: str, gnum, hcode: str, acode: str, rset: int,
                  page: int = 0, size: int = 500) -> dict:
        """한 세트의 문자중계(랠리 이벤트 목록, `payload.feed`)와 그 세트의 선발 명단.
        hcode/acode: 홈/원정 팀 코드, rset: 세트 번호. 경기 중에도 부를 수 있어 실시간 수집에 쓴다."""
        return self.get("/stat/real-time", {
            "season": season_code, "gcode": gcode, "gnum": gnum, "hcode": hcode, "acode": acode,
            "rset": rset, "page": page, "size": size,
        })

    def player_honor(self, player_code: str) -> dict:
        """선수 한 명의 수상·주요 기록(COL-04): awards(수상 내역), tripleCrowns(트리플크라운), standardRecords
        (기준 기록 달성, 예: 득점 3000개). 모두 "2025~2026 V-리그 ..." 형태의 문장 목록이다."""
        return self.get(f"/players/{player_code}/honor")
