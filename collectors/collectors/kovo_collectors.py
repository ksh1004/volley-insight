"""KOVO collectors: seasons, schedules (COL-01, 02, 04), match box scores (COL-03) and play-by-play (COL-10).

Messages keep the raw response (ELT). Keys follow requirements 2.5:
season list -> `season-list-{gcode}`, schedule -> season code, match and rally -> `{season}-{gnum}`.
A game number is unique within a season across leagues and genders (checked on 2025-26 data),
so `{season}-{gnum}` identifies one game.
"""
import logging
from dataclasses import dataclass

from .base import BaseCollector

log = logging.getLogger(__name__)

SCHEDULE_TOPIC = "kovo.schedule"
MATCH_TOPIC = "kovo.match"
RALLY_TOPIC = "kovo.rally"
GENDERS = {"1": "men", "2": "women"}
# Not real game leagues: 200 season totals, 801 all-star game, 901 pre-season exhibition.
# Game leagues: 201 regular, 202 play-off, 203 championship, 204 semi play-off.
SKIP_LEAGUES = {"200", "801", "901"}
# One set has about 250-300 events, so 500 returns a whole set in one request.
RALLY_PAGE_SIZE = 500
MAX_PAGES_PER_SET = 10


def payload_of(body):
    return body.get("payload") if isinstance(body, dict) else None


def total_pages(body) -> int:
    """Paged responses look like {"payload": {"content": [...], "page": {"totalPages": n, ...}}}."""
    payload = payload_of(body)
    page = payload.get("page") if isinstance(payload, dict) else None
    return int((page or {}).get("totalPages") or 1)


def leagues_of(season_list_body, season_code):
    """League codes for one season from a season-list response."""
    for season in payload_of(season_list_body) or []:
        if str(season.get("seasonCode")) == str(season_code):
            return [str(lg.get("leagueCode")) for lg in season.get("leagues") or []
                    if str(lg.get("leagueCode")) not in SKIP_LEAGUES]
    return []


def schedule_pages(client, season_code, gcode="001", leagues=None, genders=("1", "2"), size=200):
    """Yield (request_params, body) for every schedule page of one season."""
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
    season: str
    league: str
    gnum: int
    hcode: str
    acode: str
    sets: int  # sets played, e.g. 3:1 -> 4

    @property
    def key(self) -> str:
        return f"{self.season}-{self.gnum}"

    @classmethod
    def from_schedule(cls, item: dict) -> "Game":
        return cls(season=str(item["seasonCode"]), league=str(item["leagueCode"]), gnum=int(item["gnum"]),
                   hcode=str(item["hcode"]), acode=str(item["acode"]),
                   sets=int(item.get("hspoint") or 0) + int(item.get("aspoint") or 0))


def is_finished(item: dict) -> bool:
    """A finished game has a result (W/L from the home side) and at least 3 sets."""
    sets = int(item.get("hspoint") or 0) + int(item.get("aspoint") or 0)
    return bool(str(item.get("result") or "").strip()) and sets >= 3


def finished_games(client, season_code, gcode="001", leagues=None, genders=("1", "2")):
    """Finished games of one season, read from the schedule API (not published)."""
    games = []
    for _, body in schedule_pages(client, season_code, gcode, leagues, genders):
        games += [Game.from_schedule(g) for g in (payload_of(body) or {}).get("content") or [] if is_finished(g)]
    return sorted(games, key=lambda g: g.gnum)


def describe_games(games, gcode) -> str:
    keys = [g.key for g in games]
    shown = ", ".join(keys[:5]) + (f" ... (+{len(keys) - 5})" if len(keys) > 5 else "")
    return f"gcode={gcode} games={len(keys)} [{shown}]"


class SeasonCollector(BaseCollector):
    """Season and league codes for one competition type (gcode 001 V-League, 002 KOVO Cup)."""

    name = "kovo.seasons"
    topic = SCHEDULE_TOPIC

    def collect(self, gcode="001"):
        params = {"gcode": gcode}
        yield "/stat/season-list", params, self.client.season_list(gcode)

    def key_of(self, endpoint, request_params, body):
        return f"season-list-{request_params['gcode']}"


class ScheduleCollector(BaseCollector):
    """Every game of one season, for each league (regular, post-season...) and both genders."""

    name = "kovo.schedule"
    topic = SCHEDULE_TOPIC

    def collect(self, season_code, gcode="001", leagues=None, genders=("1", "2"), size=200):
        for params, body in schedule_pages(self.client, season_code, gcode, leagues, genders, size):
            yield "/stat/game-schedule", params, body

    def key_of(self, endpoint, request_params, body):
        return request_params["seasonCode"]


class MatchCollector(BaseCollector):
    """Box score of each finished game: game meta, every player's and both teams' records (COL-03)."""

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
    """Play-by-play of each finished game, one message per set page (COL-10)."""

    name = "kovo.rally"
    topic = RALLY_TOPIC

    def describe(self, games, gcode="001", size=RALLY_PAGE_SIZE):
        return describe_games(games, gcode)

    def collect(self, games, gcode="001", size=RALLY_PAGE_SIZE):
        for g in games:
            for rset in range(1, g.sets + 1):
                for page in range(MAX_PAGES_PER_SET):
                    body = self.client.real_time(gcode, g.season, g.gnum, g.hcode, g.acode, rset, page=page, size=size)
                    params = {"gcode": gcode, "season": g.season, "leagueCode": g.league, "gnum": g.gnum,
                              "hcode": g.hcode, "acode": g.acode, "rset": rset, "page": page, "size": size}
                    feed = (payload_of(body) or {}).get("feed") or []
                    if page == 0 and not feed:
                        log.warning("game %s set %d has no play-by-play yet", g.key, rset)
                    yield "/stat/real-time", params, body
                    if len(feed) < size:  # last page of this set
                        break
                else:
                    raise RuntimeError(f"game {g.key} set {rset} has more than {MAX_PAGES_PER_SET} pages")

    def key_of(self, endpoint, request_params, body):
        return f"{request_params['season']}-{request_params['gnum']}"
