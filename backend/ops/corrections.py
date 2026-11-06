"""ADM-03: what can be corrected, how a correction key finds its row, and applying a correction right away.

The serving row is changed at once (the site shows it immediately). The curation job (curate_kovo.py) applies the
same active corrections to the curated data on every run, so re-collection does not undo them and the season
marts (Elo, summaries) pick them up the next time that season is curated.
"""
import re

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from league.models import Player, Team
from matches.models import BoxScore, Match, PlayerMatchStat, SetScore, TeamMatchStat

from .models import Correction

T = Correction.Target
BOX = [f.name for f in BoxScore._meta.fields if f.name not in ("created_at", "updated_at")]
FIELDS = {
    T.PLAYER: ["name", "position", "height_cm", "birth_date", "nationality", "status"],
    T.TEAM: ["name", "full_name", "home_city", "home_arena"],
    T.MATCH: ["home_sets", "away_sets", "venue", "city", "broadcasters", "duration_min", "spectators",
              "referee_main", "referee_sub"],
    T.SET_SCORE: ["home_points", "away_points", "duration_min"],
    T.PLAYER_STAT: ["points", "back_number", "position"] + BOX,
    T.TEAM_STAT: ["points"] + BOX,
}
MODELS = {T.PLAYER: Player, T.TEAM: Team, T.MATCH: Match, T.SET_SCORE: SetScore,
          T.PLAYER_STAT: PlayerMatchStat, T.TEAM_STAT: TeamMatchStat}
POSITIONS = {"OH", "OP", "MB", "S", "L"}
MATCH_KEY = re.compile(r"^(\d{3})-(\d+)$")
SUB_KEY = re.compile(r"^(\d{3})-(\d+)/(\w+)$")
KEY_HELP = {
    T.PLAYER: "선수 코드 (예: 1000525)", T.TEAM: "팀 코드 (예: 1006)", T.MATCH: "시즌-경기번호 (예: 023-5)",
    T.SET_SCORE: "시즌-경기번호/세트 (예: 023-5/3)", T.PLAYER_STAT: "시즌-경기번호/선수 코드 (예: 023-5/1000525)",
    T.TEAM_STAT: "시즌-경기번호/팀 코드 (예: 023-5/1006)",
}


def _match(season, game_no):
    return Match.objects.get(competition__season__code=season, game_no=int(game_no))


def resolve(target, key):
    """The serving row a correction is about. Raises ValidationError when the key is malformed or unknown."""
    key = (key or "").strip()
    try:
        if target in (T.PLAYER, T.TEAM):
            return MODELS[target].objects.get(code=key)
        if target == T.MATCH:
            m = MATCH_KEY.match(key)
            if m:
                return _match(*m.groups())
        else:
            m = SUB_KEY.match(key)
            if m:
                season, game_no, sub = m.groups()
                match = _match(season, game_no)
                if target == T.SET_SCORE:
                    return SetScore.objects.get(match=match, set_no=int(sub))
                if target == T.PLAYER_STAT:
                    return PlayerMatchStat.objects.get(match=match, player__code=sub)
                return TeamMatchStat.objects.get(match=match, team__code=sub)
    except (ObjectDoesNotExist, ValueError, KeyError):
        raise ValidationError({"key": f"해당하는 기록이 없습니다. 형식: {KEY_HELP.get(target, '')}"})
    raise ValidationError({"key": f"형식이 맞지 않습니다. {KEY_HELP.get(target, '')}"})


def parse(target, field, text):
    """The Python value of `text` for that model field; blank = NULL for nullable fields, '' for text fields."""
    f = MODELS[target]._meta.get_field(field)
    text = (text or "").strip()
    if field == "position" and text and text not in POSITIONS:
        raise ValidationError({"value": f"포지션은 {', '.join(sorted(POSITIONS))} 중 하나입니다."})
    if not text:
        if f.null:
            return None
        if f.get_internal_type() in ("CharField", "TextField"):
            return ""
        raise ValidationError({"value": "값이 필요합니다."})
    try:
        value = f.to_python(text)
        f.run_validators(value)
    except ValidationError as e:
        raise ValidationError({"value": e.messages})
    return value


def _text(value):
    return "" if value is None else str(value)


def validate(c):
    """All problems of the form at once (target, key, field, value), then the one-active-correction rule."""
    if c.target not in FIELDS:
        raise ValidationError({"target": "대상을 고르세요."})
    errors = {}
    try:
        resolve(c.target, c.key)
    except ValidationError as e:
        errors.update(e.message_dict)
    if c.field not in FIELDS[c.target]:
        errors["field"] = [f"고칠 수 있는 항목: {', '.join(FIELDS[c.target])}"]
    else:
        try:
            parse(c.target, c.field, c.value)
        except ValidationError as e:
            errors.update(e.message_dict)
    if errors:
        raise ValidationError(errors)
    clash = Correction.objects.filter(target=c.target, key=c.key.strip(), field=c.field, is_active=True).exclude(pk=c.pk)
    if c.is_active and clash.exists():
        raise ValidationError("같은 항목에 켜져 있는 보정이 이미 있습니다. 그 보정을 고치거나 끄세요.")


def _set(obj, field, value):
    setattr(obj, field, value)
    fields = [field]
    if isinstance(obj, TeamMatchStat) and field in ("points", "attack_success", "block_success", "serve_aces"):
        obj.opponent_errors = max(0, obj.points - obj.attack_success - obj.block_success - obj.serve_aces)
        fields.append("opponent_errors")
    obj.save(update_fields=fields)


def apply(c):
    """Write the corrected value into the serving row; remember the value it replaced the first time."""
    obj = resolve(c.target, c.key)
    if not c.source_value and not Correction.objects.filter(pk=c.pk, is_active=True).exists():
        c.source_value = _text(getattr(obj, c.field))
    _set(obj, c.field, parse(c.target, c.field, c.value))


def revert(c):
    """Put back the value the correction replaced (the next curation run restores KOVO's value anyway)."""
    obj = resolve(c.target, c.key)
    _set(obj, c.field, parse(c.target, c.field, c.source_value))
