from django.contrib import admin

from .models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("published_at", "publisher", "title", "source")
    list_filter = ("source", "publisher")
    search_fields = ("title",)
    readonly_fields = [f.name for f in Article._meta.fields]
