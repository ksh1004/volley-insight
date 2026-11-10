"""ADM-02 수동 실행 양식들. 양식마다 여기서 입력을 검사하고(오타가 Airflow까지 가지 않게) DAG 실행 설정(conf)을
돌려준다. DAG도 같은 것을 한 번 더 검사한다(airflow/dags/kovo_reprocess.py 등). 관리자 '수동 실행' 화면에서 쓴다."""
import re
from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from . import corrections
from .jobs import DAGS
from .models import Correction

# 입력 형식: 시즌 "023" / "020-022" / "019,020", 경기 "023-9, 023-10", 날짜 "2026-11-05" / "2026-11-01~2026-11-05"
SEASONS = re.compile(r"^\d{1,3}(-\d{1,3})?(,\s*\d{1,3}(-\d{1,3})?)*$")
GAMES = re.compile(r"^\d{1,3}-\d+([,\s]+\d{1,3}-\d+)*$")
DATES = re.compile(r"^(\d{4}-\d{2}-\d{2})(\s*~\s*(\d{4}-\d{2}-\d{2}))?$")
MATCH = re.compile(r"^\d{3}-\d+$")


class RunForm(forms.Form):
    """모든 실행 양식의 공통 부분: 어떤 DAG를 실행하는지(dag_id)와 메모."""

    dag_id = None  # 양식마다 정한다
    note = forms.CharField(label="메모", required=False, max_length=300,
                           widget=forms.TextInput(attrs={"placeholder": "왜 실행하는지 (선택)"}))

    def conf(self) -> dict:
        """DAG에 넘길 실행 설정."""
        return {}


class NowForm(RunForm):
    """설정 없이 바로 실행해도 되는 DAG를 골라 지금 실행."""

    dag = forms.ChoiceField(label="작업", choices=[(k, f"{v['label']} ({k})") for k, v in DAGS.items() if v["run_now"]])

    def clean(self):
        data = super().clean()
        self.dag_id = data.get("dag")
        return data


class ReprocessForm(RunForm):
    """kovo_reprocess: 고른 경기·날짜·시즌을 다시 수집하거나 다시 정제."""

    dag_id = "kovo_reprocess"
    games = forms.CharField(label="경기", required=False, max_length=500,
                            widget=forms.TextInput(attrs={"placeholder": "023-9, 023-10"}))
    dates = forms.CharField(label="날짜", required=False, max_length=30,
                            widget=forms.TextInput(attrs={"placeholder": "2026-11-05 또는 2026-11-01~2026-11-05"}))
    seasons = forms.CharField(label="시즌", required=False, max_length=100,
                              widget=forms.TextInput(attrs={"placeholder": "023 또는 020-022"}))
    recollect = forms.BooleanField(label="KOVO에서 다시 받기", required=False, initial=True,
                                   help_text="경기·날짜 대상만. 끄면 KOVO에 요청하지 않고 저장된 원천으로 다시 정제만 합니다")

    def clean_games(self):
        v = self.cleaned_data["games"].strip()
        if v and not GAMES.match(v):
            raise ValidationError("시즌-경기번호를 쉼표로 나눠 적어 주세요. 예: 023-9, 023-10")
        return v

    def clean_dates(self):
        v = self.cleaned_data["dates"].strip()
        if not v:
            return v
        m = DATES.match(v)
        try:
            start = date.fromisoformat(m.group(1)) if m else None
            end = date.fromisoformat(m.group(3)) if m and m.group(3) else start
        except ValueError:
            start = None
        if start is None:
            raise ValidationError("날짜는 2026-11-05 또는 2026-11-01~2026-11-05 형식입니다.")
        if end < start or (end - start).days > 30:
            raise ValidationError("날짜 범위는 시작이 끝보다 앞이고 31일 이내여야 합니다.")
        return v

    def clean_seasons(self):
        v = self.cleaned_data["seasons"].strip()
        if v and not SEASONS.match(v):
            raise ValidationError("시즌 코드는 023 또는 020-022처럼 적어 주세요.")
        return v

    def clean(self):
        """필드별 검사가 모두 통과했는데 대상이 하나도 없으면 오류."""
        data = super().clean()
        if not any(data.get(k) for k in ("games", "dates", "seasons")) and not self.errors:
            raise ValidationError("경기, 날짜, 시즌 중 하나는 적어 주세요.")
        return data

    def conf(self):
        d = self.cleaned_data
        return {"games": d["games"], "dates": d["dates"], "seasons": d["seasons"], "recollect": d["recollect"]}


class BackfillForm(RunForm):
    """kovo_backfill: 과거 시즌 전체 수집·정제."""

    dag_id = "kovo_backfill"
    seasons = forms.CharField(label="시즌", max_length=100, widget=forms.TextInput(attrs={"placeholder": "019,020"}))
    skip_confirmed = forms.BooleanField(label="확정된 경기는 건너뛰기", required=False, initial=True,
                                        help_text="끄면 시즌 전체를 다시 받습니다 (시즌당 KOVO 요청 약 1,300번, 약 20분)")

    def clean_seasons(self):
        v = self.cleaned_data["seasons"].strip()
        if not SEASONS.match(v):
            raise ValidationError("시즌 코드는 023 또는 020-022처럼 적어 주세요.")
        return v

    def conf(self):
        return {"seasons": self.cleaned_data["seasons"], "limit": None,
                "skip_confirmed": self.cleaned_data["skip_confirmed"]}


class ReplayForm(RunForm):
    """live_replay: 끝난 경기(문자중계가 있는 2023-24부터)를 실시간 경로로 재생."""

    dag_id = "live_replay"
    match = forms.CharField(label="경기", max_length=20, widget=forms.TextInput(attrs={"placeholder": "023-6"}))
    speed = forms.FloatField(label="배속", min_value=1, max_value=600, initial=10,
                             help_text="1 = 실제 속도, 10 = 10배 (3세트 경기 약 9분)")

    def clean_match(self):
        v = self.cleaned_data["match"].strip()
        if not MATCH.match(v):
            raise ValidationError("시즌-경기번호 하나를 적어 주세요. 예: 023-6")
        if v < "020":
            raise ValidationError("문자중계는 2023-24 시즌(020)부터 있습니다.")
        try:
            match = corrections.resolve(Correction.Target.MATCH, v)
        except ValidationError:
            raise ValidationError("그런 경기가 없습니다.") from None
        if match.status not in ("FINISHED", "CONFIRMED"):
            raise ValidationError("끝난 경기만 재생할 수 있습니다.")
        return v

    def conf(self):
        return {"match": self.cleaned_data["match"], "speed": self.cleaned_data["speed"]}


# 화면의 탭 이름 → 양식
FORMS = {"now": NowForm, "reprocess": ReprocessForm, "backfill": BackfillForm, "replay": ReplayForm}
