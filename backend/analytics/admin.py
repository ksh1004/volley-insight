"""ADM-05 model management: compare the registered versions of `match-winner` and choose the serving model.

The page is the change list of ModelChange (the log of manual switches) with the MLflow versions on top.
"""
from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse

from . import registry
from .models import ModelChange


@admin.register(ModelChange)
class ModelChangeAdmin(admin.ModelAdmin):
    list_display = ("changed_at", "model_name", "from_version", "to_version", "changed_by", "reason", "predict_run")
    change_list_template = "admin/analytics/modelchange/change_list.html"

    def has_add_permission(self, request):
        return False  # a switch is made with the form on the list page

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path("promote/", self.admin_site.admin_view(self.promote), name="analytics_modelchange_promote"),
                *super().get_urls()]

    def changelist_view(self, request, extra_context=None):
        extra = {"title": "모델 관리 (서비스 모델 교체)", "model_name": registry.MODEL, "versions": [], "registry_error": "", "champion": None,
                 "can_switch": request.user.has_perm("analytics.add_modelchange") or request.user.is_superuser}
        try:
            extra["versions"] = registry.versions()
            extra["champion"] = registry.champion(extra["versions"])
        except registry.RegistryError as e:
            extra["registry_error"] = str(e)
        return super().changelist_view(request, {**(extra_context or {}), **extra})

    def promote(self, request):
        back = HttpResponseRedirect(reverse("admin:analytics_modelchange_changelist"))
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not request.user.is_superuser:
            self.message_user(request, "서비스 모델은 최고 관리자만 바꿀 수 있습니다.", messages.ERROR)
            return back
        reason = request.POST.get("reason", "").strip()
        try:
            version = int(request.POST.get("version", ""))
        except ValueError:
            version = None
        if version is None or len(reason) < 5:
            self.message_user(request, "버전을 고르고 바꾸는 사유를 5자 이상 적어 주세요.", messages.ERROR)
            return back
        try:
            vs = registry.versions()
            target = next((v for v in vs if v["version"] == version), None)
            if target is None or target["status"] != "READY":
                raise registry.RegistryError(f"v{version}은(는) 쓸 수 있는 버전이 아닙니다.")
            current = registry.champion(vs)
            if current == version:
                self.message_user(request, f"v{version}이(가) 이미 서비스 모델입니다.", messages.WARNING)
                return back
            with transaction.atomic():
                change = ModelChange.objects.create(model_name=registry.MODEL, from_version=current, to_version=version,
                                                    reason=reason, changed_by=request.user)
                registry.set_champion(version)
        except registry.RegistryError as e:
            self.message_user(request, f"바꾸지 못했습니다: {e}", messages.ERROR)
            return back
        try:
            change.predict_run = registry.trigger_predictions(f"ADM-05: v{current} -> v{version} ({reason})")
            change.save(update_fields=["predict_run"])
            self.message_user(request, f"서비스 모델을 v{version}(으)로 바꿨습니다. 다가오는 경기 예측을 새 모델로 다시 "
                                       f"만들고 있습니다(Airflow {registry.PREDICT_DAG}).", messages.SUCCESS)
        except registry.RegistryError as e:
            self.message_user(request, f"서비스 모델을 v{version}(으)로 바꿨습니다. 예측 재실행은 실패했습니다({e}). "
                                       f"다음 예측(매일 07:00)부터 새 모델이 쓰입니다.", messages.WARNING)
        return back
