"""관리자 화면: 기사 목록(읽기 전용. 기사는 Flink article-enricher가 쓴다)."""
from django.contrib import admin

from .models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("published_at", "publisher", "title", "source")
    list_filter = ("source", "publisher")
    search_fields = ("title",)
    readonly_fields = [f.name for f in Article._meta.fields]  # 모든 필드를 읽기 전용으로
