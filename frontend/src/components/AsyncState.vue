<script setup>
// Loading / error / empty states for every page. Loading shows a skeleton that keeps the layout in place
// (design system: no blank screen or bare "loading" text) and marks the region busy for screen readers.
defineProps({
  loading: Boolean,
  error: [String, Object, null],
  empty: Boolean,
  emptyText: { type: String, default: '데이터가 없습니다.' },
  rows: { type: Number, default: 4 }, // skeleton lines
})
</script>

<template>
  <div v-if="loading" class="skeleton" aria-busy="true" aria-live="polite">
    <span class="sr-only">불러오는 중</span>
    <div v-for="i in rows" :key="i" class="bar" :style="{ width: `${100 - (i % 3) * 12}%` }" />
  </div>
  <div v-else-if="error" class="state error" role="alert">
    데이터를 불러오지 못했습니다. <span v-if="typeof error === 'string'">{{ error }}</span>
    <button class="tab retry" type="button" @click="$router.go(0)">다시 시도</button>
  </div>
  <div v-else-if="empty" class="state">{{ emptyText }}</div>
  <slot v-else />
</template>

<style scoped>
.skeleton { display: grid; gap: 10px; padding: var(--space-3) 0; }
.bar { height: 14px; border-radius: 6px; background: linear-gradient(90deg, var(--color-muted) 0%, var(--color-border-soft) 50%, var(--color-muted) 100%);
       background-size: 200% 100%; animation: shimmer 1.2s ease-in-out infinite; }
@keyframes shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
.state { color: var(--color-muted-foreground); padding: var(--space-5) 0; text-align: center; }
.error { color: var(--color-bad); }
.retry { margin-left: var(--space-2); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
