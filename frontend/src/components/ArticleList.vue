<script setup>
// Article rows (WEB-09, WEB-14): title opens the publisher's page in a new tab; we show only title, summary
// and publisher (NFR-06). `highlight` renders search marks: the API escapes the text (encoder=html) and adds
// only <mark>, so v-html is limited to that.
// Motion: rows rise in one after another; the title underline draws from the left on hover.
import { fmtAgo, fmtFullDate } from '@/utils/format'

defineProps({
  articles: { type: Array, required: true },
  compact: Boolean, // no summary (side cards)
  highlight: Boolean,
})
</script>

<template>
  <ul class="articles" :class="{ compact }">
    <li v-for="(a, i) in articles" :key="a.url" v-reveal="i" class="article">
      <a :href="a.url" target="_blank" rel="noopener noreferrer" class="title">
        <span v-if="highlight && a.highlight?.title" v-html="a.highlight.title" />
        <span v-else>{{ a.title }}</span>
        <svg class="ext" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3h7v7M13 3 6 10M11 13H3V5" /></svg>
        <span class="sr-only">(새 창, {{ a.publisher }})</span>
      </a>
      <p v-if="!compact && (a.summary || a.highlight?.summary)" class="summary">
        <span v-if="highlight && a.highlight?.summary" v-html="a.highlight.summary" />
        <span v-else>{{ a.summary }}</span>
      </p>
      <div class="meta small">
        <span class="publisher">{{ a.publisher }}</span>
        <time :datetime="a.published_at" :title="a.published_at ? fmtFullDate(a.published_at) : ''">{{ fmtAgo(a.published_at) }}</time>
        <span v-if="(a.teams || []).length" class="tags">
          <RouterLink v-for="t in a.teams" :key="t.code" :to="`/teams/${t.code}`" class="tag">{{ t.name }}</RouterLink>
          <RouterLink v-for="p in a.players || []" :key="p.code" :to="`/players/${p.code}`" class="tag player">{{ p.name }}</RouterLink>
        </span>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.articles { list-style: none; margin: 0; padding: 0; }
.article { padding: 12px 0; border-bottom: 1px solid var(--color-border-soft); }
.article:last-child { border-bottom: 0; }
.compact .article { padding: 8px 0; }
.title { color: var(--color-foreground); font-weight: 500; line-height: 1.45; display: inline; }
.title:hover { color: var(--color-primary); text-decoration: none; }
.title > span:first-child { background: linear-gradient(currentColor, currentColor) 0 100% / 0 1px no-repeat;
                            transition: background-size var(--dur-base) var(--ease-out); }
.title:hover > span:first-child, .title:focus-visible > span:first-child { background-size: 100% 1px; }
.ext { width: 12px; height: 12px; margin-left: 4px; vertical-align: -1px; fill: none; stroke: var(--color-muted-foreground); stroke-width: 1.6; }
.summary { margin: 4px 0 0; color: var(--color-muted-foreground); font-size: .9rem; display: -webkit-box;
           -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; margin-top: 6px; color: var(--color-muted-foreground); }
.publisher { font-weight: 500; color: var(--color-foreground); }
.tags { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.tag { padding: 0 8px; border-radius: 999px; background: var(--color-primary-soft); color: var(--color-primary); font-size: .78rem; line-height: 1.7; }
.tag.player { background: var(--color-accent-soft); color: var(--color-accent); }
.tag:hover { text-decoration: none; outline: 1px solid currentColor; }
:deep(mark) { background: var(--color-highlight); color: inherit; border-radius: 2px; padding: 0 1px; }
@media (pointer: coarse) { .tag { line-height: 2.2; } }
</style>
