"""KOVO 수집기: 시즌 목록, 경기 일정(COL-01, 02, 04), 경기별 박스스코어(COL-03), 문자중계(COL-10).

메시지에는 API 응답 원본을 그대로 담는다(ELT). Kafka 메시지 키는 요구사항 2.5를 따른다.
  시즌 목록 → `season-list-{gcode}`, 일정 → 시즌 코드, 경기 기록·문자중계 → `{시즌}-{경기번호}`
경기 번호(gnum)는 한 시즌 안에서 리그·성별을 통틀어 겹치지 않는다(2025-26 데이터로 확인).
그래서 `{시즌}-{경기번호}`(예: "023-6")만으로 경기 하나가 정해진다.

용어: 시즌 코드는 "001"(2004-05)부터 차례로 붙는 3자리 문자열이고, 이 프로젝트 전체에서 문자열로 다룬다.
"""
import logging
from dataclasses import dataclass

from .base import BaseCollector

log = logging.getLogger(__name__)

# 수집 데이터 종류별 Kafka 토픽
SCHEDULE_TOPIC = "kovo.schedule"
MATCH_TOPIC = "kovo.match"
RALLY_TOPIC = "kovo.rally"
GENDERS = {"1": "men", "2": "women"}  # KOVO API의 성별 코드
# 실제 경기가 아닌 리그 코드: 200 시즌 통산, 801 올스타전, 901 시범경기, 903 연습경기(2014-15에서 확인)
# 경기 리그 코드: 201 정규리그, 202 플레이오프, 203 챔피언결정전, 204 준플레이오프
SKIP_LEAGUES = {"200", "801", "901", "903"}
# 한 세트는 이벤트가 250~300개 정도라, 500개씩 받으면 대개 한 번에 한 세트가 다 온다
RALLY_PAGE_SIZE = 500
MAX_PAGES_PER_SET = 10  # 이보다 많으면 무언가 잘못된 것으로 보고 멈춘다(무한 반복 방지)
# KOVO 문자중계는 2023-24(시즌 020)부터 있다. 001~019 시즌은 빈 목록을 돌려주고 020·021은 이벤트가 있다
# (2026-11-03 확인). 그래서 그 이전 경기는 요청하지 않는다.
PLAY_BY_PLAY_FROM = "020"


def payload_of(body):
    """KOVO 응답에서 실제 데이터 부분(`payload`)을 꺼낸다. 형식이 다르면 None."""
    return body.get("payload") if isinstance(body, dict) else None


def total_pages(body) -> int:
    """페이지로 나뉜 응답의 전체 페이지 수. 형식: {"payload": {"content": [...], "page": {"totalPages": n, ...}}}"""
    payload = payload_of(body)
    page = payload.get("page") if isinstance(payload, dict) else None
    return int((page or {}).get("totalPages") or 1)


def leagues_of(season_list_body, season_code):
    """시즌 목록 응답에서 한 시즌의 리그 코드 목록을 뽑는다(실제 경기가 아닌 리그는 뺌)."""
    for season in payload_of(season_list_body) or []:
        if str(season.get("seasonCode")) == str(season_code):
            return [str(lg.get("leagueCode")) for lg in season.get("leagues") or []
                    if str(lg.get("leagueCode")) not in SKIP_LEAGUES]
    return []


def schedule_pages(client, season_code, gcode="001", leagues=None, genders=("1", "2"), size=200):
    """한 시즌의 모든 일정 페이지를 (요청 파라미터, 응답) 쌍으로 하나씩 내보낸다.
    리그 × 성별 × 페이지 순서로 돈다. leagues를 안 주면 시즌 목록 API로 먼저 알아낸다."""
    if leagues is None:
        leagues = leagues_of(client.season_list(gcode), season_code)
        if not leagues:
            raise ValueError(f"season {season_code} not found in season list (gcode={gcode})")
    for league in leagues:
        for gender in genders:
            page = 0
            while True:
                body = client.game_schedule(gcode, season_code, league, gender, page=page, size=size)
                params = {"gcode": gcode, "seasonCode": season_code, "leagueCode": league,
                          "gender": gender, "page": page, "size": size}
                yield params, body
                page += 1
                if page >= total_pages(body):
                    break


@dataclass(frozen=True)
class Game:
    """수집 대상 경기 하나. 일정 API 항목에서 만들며, 상세·문자중계 요청에 필요한 값만 담는다."""

    season: str  # 시즌 코드, 예: "023"
    league: str  # 리그 코드, 예: "201" 정규리그
    gnum: int  # 시즌 안의 경기 번호
    hcode: str  # 홈 팀 코드
    acode: str  # 원정 팀 코드
    sets: int  # 치른 세트 수. 예: 3:1이면 4

    @property
    def key(self) -> str:
        """경기 키 "{시즌}-{경기번호}". Kafka 메시지 키와 화면 주소(/matches/023-6)에 쓴다."""
        return f"{self.season}-{self.gnum}"

    @classmethod
    def from_schedule(cls, item: dict) -> "Game":
        """일정 API의 경기 항목 하나로 Game을 만든다. hspoint/aspoint는 홈/원정이 이긴 세트 수."""
        return cls(season=str(item["seasonCode"]), league=str(item["leagueCode"]), gnum=int(item["gnum"]),
                   hcode=str(item["hcode"]), acode=str(item["acode"]),
                   sets=int(item.get("hspoint") or 0) + int(item.get("aspoint") or 0))


def is_finished(item: dict) -> bool:
    """끝난 경기인가: 결과(홈 기준 승/패)가 있고 세트가 3개 이상이다."""
    sets = int(item.get("hspoint") or 0) + int(item.get("aspoint") or 0)
    return bool(str(item.get("result") or "").strip()) and sets >= 3


def finished_games(client, season_code, gcode="001", leagues=None, genders=("1", "2")):
    """일정 API를 읽어 한 시즌의 끝난 경기 목록을 경기 번호 순으로 돌려준다(Kafka로 보내지는 않음)."""
    games = []
    for _, body in schedule_pages(client, season_code, gcode, leagues, genders):
        games += [Game.from_schedule(g) for g in (payload_of(body) or {}).get("content") or [] if is_finished(g)]
    return sorted(games, key=lambda g: g.gnum)


def describe_games(games, gcode) -> str:
    """수집 로그에 적을 경기 목록 요약. 앞의 5개만 보여 주고 나머지는 개수로 줄인다."""
    keys = [g.key for g in games]
    shown = ", ".join(keys[:5]) + (f" ... (+{len(keys) - 5})" if len(keys) > 5 else "")
    return f"gcode={gcode} games={len(keys)} [{shown}]"


class SeasonCollector(BaseCollector):
    """대회 종류 하나(gcode 001 V-리그, 002 KOVO컵)의 시즌·리그 코드 목록을 수집한다."""

    name = "kovo.seasons"
    topic = SCHEDULE_TOPIC

    def __init__(self, client, publisher):
        super().__init__(client, publisher)
        self.last_body = None  # 가장 최근 실행의 응답. 호출하는 쪽이 현재 시즌을 찾을 때 다시 요청하지 않고 쓴다

    def collect(self, gcode="001"):
        params = {"gcode": gcode}
        self.last_body = self.client.season_list(gcode)
        yield "/stat/season-list", params, self.last_body

    def key_of(self, endpoint, request_params, body):
        return f"season-list-{request_params['gcode']}"


class ScheduleCollector(BaseCollector):
    """한 시즌의 모든 경기(정규리그·포스트시즌 등 리그별, 남녀부 모두) 일정과 결과를 수집한다."""

    name = "kovo.schedule"
    topic = SCHEDULE_TOPIC

    def __init__(self, client, publisher):
        super().__init__(client, publisher)
        self.games = []  # 최근 실행에서 본 일정 항목들. 호출하는 쪽이 같은 요청을 다시 하지 않게 남겨 둔다

    def collect(self, season_code, gcode="001", leagues=None, genders=("1", "2"), size=200):
        self.games = []
        for params, body in schedule_pages(self.client, season_code, gcode, leagues, genders, size):
            self.games += (payload_of(body) or {}).get("content") or []
            yield "/stat/game-schedule", params, body

    def finished_games(self):
        """최근 실행에서 본 경기 중 끝난 경기만, 경기 번호 순으로."""
        return sorted((Game.from_schedule(g) for g in self.games if is_finished(g)), key=lambda g: g.gnum)

    def key_of(self, endpoint, request_params, body):
        return request_params["seasonCode"]


class MatchCollector(BaseCollector):
    """끝난 경기마다 박스스코어를 수집한다: 경기 정보, 모든 선수와 두 팀의 기록(COL-03)."""

    name = "kovo.match"
    topic = MATCH_TOPIC

    def describe(self, games, gcode="001"):
        return describe_games(games, gcode)

    def collect(self, games, gcode="001"):
        for g in games:
            params = {"gcode": gcode, "seasonCode": g.season, "leagueCode": g.league, "gnum": g.gnum}
            yield f"/stat/game-schedule/{g.gnum}", params, self.client.game_detail(gcode, g.season, g.league, g.gnum)

    def key_of(self, endpoint, request_params, body):
        return f"{request_params['seasonCode']}-{request_params['gnum']}"


class RallyCollector(BaseCollector):
    """끝난 경기마다 문자중계(랠리별 이벤트)를 수집한다. 세트의 페이지 하나가 메시지 하나다(COL-10)."""

    name = "kovo.rally"
    topic = RALLY_TOPIC

    def describe(self, games, gcode="001", size=RALLY_PAGE_SIZE):
        return describe_games(games, gcode)

    def collect(self, games, gcode="001", size=RALLY_PAGE_SIZE):
        old = [g for g in games if g.season < PLAY_BY_PLAY_FROM]
        if old:
            log.info("skipping %d game(s) before season %s: KOVO has no play-by-play for them",
                     len(old), PLAY_BY_PLAY_FROM)
        for g in games:
            if g.season < PLAY_BY_PLAY_FROM:
                continue
            for rset in range(1, g.sets + 1):  # 1세트부터 마지막 세트까지
                for page in range(MAX_PAGES_PER_SET):
                    body = self.client.real_time(gcode, g.season, g.gnum, g.hcode, g.acode, rset, page=page, size=size)
                    params = {"gcode": gcode, "season": g.season, "leagueCode": g.league, "gnum": g.gnum,
                              "hcode": g.hcode, "acode": g.acode, "rset": rset, "page": page, "size": size}
                    feed = (payload_of(body) or {}).get("feed") or []
                    if page == 0 and not feed:
                        log.warning("game %s set %d has no play-by-play yet", g.key, rset)
                    yield "/stat/real-time", params, body
                    if len(feed) < size:  # 꽉 차지 않은 페이지 = 이 세트의 마지막 페이지
                        break
                else:  # for-else: break 없이 끝났다 = 페이지 상한에 걸렸다
                    raise RuntimeError(f"game {g.key} set {rset} has more than {MAX_PAGES_PER_SET} pages")
                if rset == 1 and page == 0 and not feed:
                    break  # 아직 이 경기의 문자중계가 올라오지 않았다. 다음 실행에서 다시 요청한다

    def key_of(self, endpoint, request_params, body):
        return f"{request_params['season']}-{request_params['gnum']}"
