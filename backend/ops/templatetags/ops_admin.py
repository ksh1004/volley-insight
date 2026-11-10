"""관리자 첫 화면용 템플릿 태그: 아직 아무도 확인하지 않은 작업 실패 알림 수(PIPE-09).
templates/admin/ops/index.html에서 {% open_job_alerts as n %}로 불러 배지로 보여 준다."""
from django import template

from ops.models import JobAlert

register = template.Library()


@register.simple_tag
def open_job_alerts():
    """OPEN(미확인) 상태인 실패 알림 수."""
    return JobAlert.objects.filter(status=JobAlert.Status.OPEN).count()
