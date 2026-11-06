"""Template tags for the admin index: the number of job failures nobody has checked yet (PIPE-09)."""
from django import template

from ops.models import JobAlert

register = template.Library()


@register.simple_tag
def open_job_alerts():
    return JobAlert.objects.filter(status=JobAlert.Status.OPEN).count()
