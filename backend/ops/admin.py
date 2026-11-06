"""Admin screens: data quality (PIPE-10), corrections (ADM-03), job status and failures (ADM-01, PIPE-09) and
manual runs (ADM-02). Django admin is bound to localhost only."""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from . import corrections, jobs
from .forms import FORMS
from .models import Correction, JobAlert, ManualRun, QualityIssue

# whole sources missing: re-collection may fill them, a correction cannot
UNFIXABLE = {QualityIssue.Rule.MISSING_RALLY, QualityIssue.Rule.RALLY_SCORE_MISMATCH, QualityIssue.Rule.MISSING_BOX_SCORE}


@admin.register(QualityIssue)
class QualityIssueAdmin(admin.ModelAdmin):
    list_display = ("where", "rule", "severity", "status", "subject", "detail", "last_seen_at")
    list_filter = ("status", "severity", "rule", "season_code")
    search_fields = ("subject", "detail", "note")
    list_select_related = ("match__home_team", "match__away_team")
    readonly_fields = ("where", "match", "rule", "severity", "subject", "detail", "first_seen_at", "last_seen_at",
                       "resolved_at", "acknowledged_by", "fix_link")
    fields = ("where", "match", "rule", "severity", "subject", "detail", "status", "note", "acknowledged_by",
              "first_seen_at", "last_seen_at", "resolved_at", "fix_link")
    actions = ["acknowledge", "reopen"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="경기", ordering="game_no")
    def where(self, obj):
        return f"{obj.season_code}-{obj.game_no}" + (f" · {obj.set_no}세트" if obj.set_no else "")

    @admin.display(description="보정")
    def fix_link(self, obj):
        if obj.rule in UNFIXABLE:
            return "문자중계·선수 기록이 통째로 없는 경우는 보정할 수 없습니다. KOVO에도 없으면 '확인함'으로 표시하세요."
        target, key = Correction.Target.MATCH, f"{obj.season_code}-{obj.game_no}"
        if obj.rule in (QualityIssue.Rule.SET_COUNT_MISMATCH, QualityIssue.Rule.INVALID_SET_SCORE) and obj.set_no:
            target, key = Correction.Target.SET_SCORE, f"{key}/{obj.set_no}"
        elif obj.rule == QualityIssue.Rule.PLAYER_NAME_MISSING:
            target, key = Correction.Target.PLAYER, obj.subject
        elif obj.subject:  # team codes have 4 digits, player codes 7
            target = Correction.Target.PLAYER_STAT if len(obj.subject) > 4 else Correction.Target.TEAM_STAT
            key = f"{key}/{obj.subject}"
        url = reverse("admin:ops_correction_add") + f"?target={target}&key={key}"
        return format_html('<a href="{}">이 기록 보정하기</a> (다음 정제 때 검사를 다시 합니다)', url)

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data and obj.status == QualityIssue.Status.ACKNOWLEDGED:
            obj.acknowledged_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="확인함으로 표시 (원천 누락 등, 다음 실행에도 유지)")
    def acknowledge(self, request, queryset):
        n = queryset.exclude(status=QualityIssue.Status.RESOLVED).update(
            status=QualityIssue.Status.ACKNOWLEDGED, acknowledged_by=request.user)
        self.message_user(request, f"{n}건을 확인함으로 표시했습니다. 메모는 각 항목에서 남기세요.")

    @admin.action(description="미해결로 되돌리기")
    def reopen(self, request, queryset):
        n = queryset.filter(status=QualityIssue.Status.ACKNOWLEDGED).update(status=QualityIssue.Status.OPEN)
        self.message_user(request, f"{n}건을 미해결로 되돌렸습니다.")


@admin.register(Correction)
class CorrectionAdmin(admin.ModelAdmin):
    list_display = ("target", "key", "field", "source_value", "value", "is_active", "created_by", "updated_at")
    list_filter = ("is_active", "target")
    search_fields = ("key", "reason", "value")
    readonly_fields = ("current_value", "source_value", "created_by", "created_at", "updated_at", "guide")
    fieldsets = (
        (None, {"fields": ("guide", "target", "key", "field", "current_value", "value", "reason", "is_active")}),
        ("기록", {"fields": ("source_value", "created_by", "created_at", "updated_at")}),
    )
    actions = ["deactivate"]

    def has_delete_permission(self, request, obj=None):
        return False  # keep the history; turn a correction off instead

    def get_changeform_initial_data(self, request):
        return {k: request.GET[k] for k in ("target", "key", "field") if k in request.GET}

    @admin.display(description="고칠 수 있는 항목")
    def guide(self, obj):
        rows = format_html_join("", "<li><b>{}</b> — 키: {} · 항목: {}</li>",
                                ((label, corrections.KEY_HELP[t], ", ".join(corrections.FIELDS[t]))
                                 for t, label in Correction.Target.choices))
        return format_html("<ul style='margin:0;padding-left:16px'>{}</ul><p>저장하면 사이트에 바로 반영됩니다. "
                           "시즌 집계(Elo, 시즌 기록)는 그 시즌을 다음에 정제할 때 반영됩니다.</p>", rows)

    @admin.display(description="지금 사이트 값")
    def current_value(self, obj):
        if not obj or not obj.pk:
            return "-"
        try:
            return repr(getattr(corrections.resolve(obj.target, obj.key), obj.field))
        except (ValidationError, AttributeError):
            return "-"

    def save_model(self, request, obj, form, change):
        was_active = change and Correction.objects.filter(pk=obj.pk, is_active=True).exists()
        if not change:
            obj.created_by = request.user
        obj.key = obj.key.strip()
        with transaction.atomic():
            if obj.is_active:
                corrections.apply(obj)
            elif was_active:
                corrections.revert(obj)
            super().save_model(request, obj, form, change)

    @admin.action(description="끄기 (원래 값으로 되돌림)")
    def deactivate(self, request, queryset):
        n = 0
        with transaction.atomic():
            for c in queryset.filter(is_active=True):
                corrections.revert(c)
                c.is_active = False
                c.save(update_fields=["is_active", "updated_at"])
                n += 1
        self.message_user(request, f"보정 {n}건을 껐습니다.", messages.SUCCESS)


@admin.register(JobAlert)
class JobAlertAdmin(admin.ModelAdmin):
    """ADM-01 수집 현황: Airflow job status and collector runs on top, the failure list (PIPE-09 alerts) below."""
    change_list_template = "admin/ops/jobalert/change_list.html"
    list_display = ("created_at", "job", "short_error", "delivery", "status", "airflow_link")
    list_filter = ("status", "dag_id")
    search_fields = ("dag_id", "task_id", "error", "note")
    readonly_fields = ("dag_id", "task_id", "run_id", "map_index", "try_number", "error", "airflow_link", "delivery",
                       "created_at", "acknowledged_by")
    fields = ("dag_id", "task_id", "run_id", "try_number", "created_at", "error", "airflow_link", "delivery",
              "status", "note", "acknowledged_by")
    actions = ["acknowledge"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="작업", ordering="dag_id")
    def job(self, obj):
        label = jobs.DAGS.get(obj.dag_id, {}).get("label", "")
        return format_html("{}<br><span class='quiet'>{} › {}</span>", label or obj.dag_id, obj.dag_id,
                           obj.task_id or "실행 전체")

    @admin.display(description="오류")
    def short_error(self, obj):
        first = (obj.error or "").strip().splitlines()
        return (first[0][:160] + ("…" if len(first[0]) > 160 else "")) if first else "-"

    @admin.display(description="Airflow")
    def airflow_link(self, obj):
        if not obj.log_url:
            return "-"
        return format_html('<a href="{}" target="_blank" rel="noopener">로그 보기</a>', obj.log_url)

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data and obj.status == JobAlert.Status.ACKNOWLEDGED:
            obj.acknowledged_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="확인함으로 표시", permissions=["change"])
    def acknowledge(self, request, queryset):
        n = queryset.filter(status=JobAlert.Status.OPEN).update(status=JobAlert.Status.ACKNOWLEDGED,
                                                               acknowledged_by=request.user)
        self.message_user(request, f"{n}건을 확인함으로 표시했습니다.")

    def changelist_view(self, request, extra_context=None):
        extra = {"title": "수집 현황", "dags": [], "airflow_error": "", "collection": None, "collection_error": "",
                 "airflow_ui": jobs.AIRFLOW_UI, "open_alerts": JobAlert.objects.filter(status=JobAlert.Status.OPEN).count(),
                 "webhook_hint": "메신저 알림이 꺼져 있습니다. .env에 ALERT_WEBHOOK_URL(Mattermost·Slack·Discord 웹훅)을 "
                                 "넣고 airflow를 다시 만들면 실패할 때 메신저로도 알립니다.",
                 "webhook_missing": JobAlert.objects.exclude(delivery="").exclude(delivery__startswith="REPEAT").values_list("delivery", flat=True)
                                    .first() == "NO_WEBHOOK"}
        try:
            extra["dags"] = jobs.overview()
        except jobs.ServiceError as e:
            extra["airflow_error"] = str(e)
        try:
            extra["collection"] = jobs.collection_runs()
        except Exception as e:  # noqa: BLE001 - Elasticsearch down or index missing: say so on the page
            extra["collection_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return super().changelist_view(request, {**(extra_context or {}), **extra})


@admin.register(ManualRun)
class ManualRunAdmin(admin.ModelAdmin):
    """ADM-02 수동 실행: forms that start Airflow DAGs, and the log of what was started."""
    change_list_template = "admin/ops/manualrun/change_list.html"
    list_display = ("requested_at", "job", "config_text", "note", "requested_by", "airflow_link")
    list_filter = ("dag_id",)

    def has_add_permission(self, request):
        return False  # runs are started with the forms on the list page

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="작업", ordering="dag_id")
    def job(self, obj):
        return f"{jobs.DAGS.get(obj.dag_id, {}).get('label', obj.dag_id)} ({obj.dag_id})"

    @admin.display(description="설정")
    def config_text(self, obj):
        return ", ".join(f"{k}={v}" for k, v in obj.conf.items() if v not in ("", None)) or "-"

    @admin.display(description="Airflow")
    def airflow_link(self, obj):
        if not obj.run_id:
            return "-"
        return format_html('<a href="{}" target="_blank" rel="noopener">실행 보기</a>', jobs.run_url(obj.dag_id, obj.run_id))

    def get_urls(self):
        return [path("run/", self.admin_site.admin_view(self.run), name="ops_manualrun_run"), *super().get_urls()]

    def can_run(self, request):
        return request.user.is_superuser or request.user.has_perm("ops.add_manualrun")

    def changelist_view(self, request, extra_context=None):
        bound = getattr(request, "_run_form", None)
        run_forms = {kind: (bound if bound is not None and bound.prefix == kind else cls(prefix=kind))
                     for kind, cls in FORMS.items()}
        extra = {"title": "수동 실행", "forms": run_forms, "can_run": self.can_run(request), "dags": jobs.DAGS,
                 "airflow_ui": jobs.AIRFLOW_UI}
        return super().changelist_view(request, {**(extra_context or {}), **extra})

    def run(self, request):
        back = HttpResponseRedirect(reverse("admin:ops_manualrun_changelist"))
        if request.method != "POST":
            return back
        if not self.can_run(request):
            self.message_user(request, "작업을 실행할 권한이 없습니다.", messages.ERROR)
            return back
        kind = request.POST.get("kind", "")
        if kind not in FORMS:
            self.message_user(request, "알 수 없는 실행 종류입니다.", messages.ERROR)
            return back
        form = FORMS[kind](request.POST, prefix=kind)
        if not form.is_valid():
            self.message_user(request, "입력을 확인해 주세요. 아래 양식에 이유가 적혀 있습니다.", messages.ERROR)
            request._run_form = form
            request.method = "GET"  # render the list page with the bound form and its errors
            return self.changelist_view(request)
        note = form.cleaned_data.get("note", "")
        conf = form.conf()
        label = jobs.DAGS.get(form.dag_id, {}).get("label", form.dag_id)
        try:
            run_id = jobs.trigger(form.dag_id, conf, f"ADM-02 {request.user.get_username()}: {note}".strip(": "))
        except jobs.ServiceError as e:
            self.message_user(request, f"실행하지 못했습니다: {e}", messages.ERROR)
            return back
        ManualRun.objects.create(dag_id=form.dag_id, conf=conf, note=note, requested_by=request.user, run_id=run_id)
        self.message_user(request, format_html(
            "{}({})을(를) 실행했습니다. 진행 상황은 <a href='{}' target='_blank' rel='noopener'>Airflow</a>에서 볼 수 있고, "
            "실패하면 수집 현황에 올라옵니다.", label, form.dag_id, jobs.run_url(form.dag_id, run_id)), messages.SUCCESS)
        return back


admin.site.site_header = "배구 분석 관리"
admin.site.index_template = "admin/ops/index.html"  # adds the open job-failure notice on top
admin.site.site_title = "배구 분석 관리"
admin.site.index_title = "데이터 관리"
