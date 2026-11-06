"""ADM-02 manual-run forms. Each form checks its input here (so a typo never reaches Airflow) and returns the
DAG run config; the DAGs check the same things again (airflow/dags/kovo_reprocess.py and others)."""
import re
from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from . import corrections
from .jobs import DAGS
from .models import Correction

SEASONS = re.compile(r"^\d{1,3}(-\d{1,3})?(,\s*\d{1,3}(-\d{1,3})?)*$")
GAMES = re.compile(r"^\d{1,3}-\d+([,\s]+\d{1,3}-\d+)*$")
DATES = re.compile(r"^(\d{4}-\d{2}-\d{2})(\s*~\s*(\d{4}-\d{2}-\d{2}))?$")
MATCH = re.compile(r"^\d{3}-\d+$")


class RunForm(forms.Form):
    dag_id = None  # set by each form
    note = forms.CharField(label="메모", required=False, max_length=300,
                           widget=forms.TextInput(attrs={"placeholder": "왜 실행하는지 (선택)"}))

    def conf(self) -> dict:
        return {}


class NowForm(RunForm):
    dag = forms.ChoiceField(label="작업", choices=[(k, f"{v['label']} ({k})") for k, v in DAGS.items() if v["run_now"]])

    def clean(self):
        data = super().clean()
        self.dag_id = data.get("dag")
        return data


class ReprocessForm(RunForm):
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
        data = super().clean()
        if not any(data.get(k) for k in ("games", "dates", "seasons")) and not self.errors:
            raise ValidationError("경기, 날짜, 시즌 중 하나는 적어 주세요.")
        return data

    def conf(self):
        d = self.cleaned_data
        return {"games": d["games"], "dates": d["dates"], "seasons": d["seasons"], "recollect": d["recollect"]}


class BackfillForm(RunForm):
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


FORMS = {"now": NowForm, "reprocess": ReprocessForm, "backfill": BackfillForm, "replay": ReplayForm}
