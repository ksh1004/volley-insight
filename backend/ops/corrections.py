"""ADM-03 데이터 보정: 무엇을 고칠 수 있는지, 보정 키로 어떤 행을 찾는지, 보정을 바로 적용하는 방법.

서비스 DB의 행은 저장 즉시 바뀐다(사이트에 바로 보임). 정제 작업(curate_kovo.py)도 매번 같은 켜진 보정을 정제
데이터에 적용하므로, 다시 수집해도 보정이 되돌아가지 않고, 그 시즌이 다음에 정제될 때 시즌 마트(Elo, 요약)에도
반영된다.
"""
import re

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from league.models import Player, Team
from matches.models import BoxScore, Match, PlayerMatchStat, SetScore, TeamMatchStat

from .models import Correction

T = Correction.Target
# 박스스코어 기록 필드 전부(생성·수정 시각 제외)
BOX = [f.name for f in BoxScore._meta.fields if f.name not in ("created_at", "updated_at")]
# 대상별로 고칠 수 있는 항목
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
MATCH_KEY = re.compile(r"^(\d{3})-(\d+)$")  # 경기 키: 023-5
SUB_KEY = re.compile(r"^(\d{3})-(\d+)/(\w+)$")  # 경기 하위 키: 023-5/3(세트), 023-5/1000525(선수·팀 코드)
# 대상별 키 형식 안내(오류 문구에 씀)
KEY_HELP = {
    T.PLAYER: "선수 코드 (예: 1000525)", T.TEAM: "팀 코드 (예: 1006)", T.MATCH: "시즌-경기번호 (예: 023-5)",
    T.SET_SCORE: "시즌-경기번호/세트 (예: 023-5/3)", T.PLAYER_STAT: "시즌-경기번호/선수 코드 (예: 023-5/1000525)",
    T.TEAM_STAT: "시즌-경기번호/팀 코드 (예: 023-5/1006)",
}


def _match(season, game_no):
    """시즌 코드와 경기 번호로 경기를 찾는다(없으면 DoesNotExist)."""
    return Match.objects.get(competition__season__code=season, game_no=int(game_no))


def resolve(target, key):
    """보정이 가리키는 서비스 DB 행. 키 형식이 틀리거나 그런 기록이 없으면 ValidationError(관리자 양식에 표시)."""
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
    """보정 값 문자열을 그 모델 필드의 파이썬 값으로 바꾼다(필드의 형식 검사 포함).
    빈 값: NULL 허용 필드면 None, 글자 필드면 '', 그 밖에는 오류. 포지션은 정해진 코드만 허용."""
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
    """값을 원래 값 보관용 문자열로(None은 '')."""
    return "" if value is None else str(value)


def validate(c):
    """양식의 모든 문제(대상, 키, 항목, 값)를 한꺼번에 모아 보여 주고, 그다음 '같은 항목에 켜진 보정은 하나' 규칙을 본다."""
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
    """행에 값을 넣고 저장한다. 팀 기록의 득점 관련 값을 바꾸면 '상대 범실 득점'도 다시 계산한다."""
    setattr(obj, field, value)
    fields = [field]
    if isinstance(obj, TeamMatchStat) and field in ("points", "attack_success", "block_success", "serve_aces"):
        obj.opponent_errors = max(0, obj.points - obj.attack_success - obj.block_success - obj.serve_aces)
        fields.append("opponent_errors")
    obj.save(update_fields=fields)


def apply(c):
    """고친 값을 서비스 DB 행에 쓴다. 처음 적용할 때만 원래 값을 source_value에 기억해 둔다(끌 때 되돌리기 위해)."""
    obj = resolve(c.target, c.key)
    if not c.source_value and not Correction.objects.filter(pk=c.pk, is_active=True).exists():
        c.source_value = _text(getattr(obj, c.field))
    _set(obj, c.field, parse(c.target, c.field, c.value))


def revert(c):
    """보정 전 값으로 되돌린다(어차피 다음 정제 때 KOVO 값으로 복원되지만, 사이트에 바로 반영하려고)."""
    obj = resolve(c.target, c.key)
    _set(obj, c.field, parse(c.target, c.field, c.source_value))
