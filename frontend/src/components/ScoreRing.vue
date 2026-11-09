<script setup>
// A 1-99 score as a ring that fills when first seen (the number in the middle counts up with it).
// The ring repeats the number; screen readers get the label with the number once.
import { computed, ref } from 'vue'
import { DURATION, ease, useInView, useTween } from '@/motion/core'

const props = defineProps({
  value: { type: Number, default: null },
  label: { type: String, default: '종합' },
  size: { type: Number, default: 96 },
})

const el = ref(null)
const seen = useInView(el)
const shown = useTween(() => (seen.value && props.value != null ? props.value : 0), {
  duration: DURATION.chart, easing: ease.out, from: 0 })
const R = 42
const dash = computed(() => `${Math.max(0, Math.min(99, shown.value))} 100`)
</script>

<template>
  <div ref="el" class="ring" :style="{ width: `${size}px`, height: `${size}px`, '--s': size }" role="img"
       :aria-label="value == null ? `${label} 점수 없음` : `${label} ${value}점 (99점 만점)`">
    <svg viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" :r="R" class="track" />
      <circle cx="50" cy="50" :r="R" class="fill" pathLength="100" :stroke-dasharray="dash" />
    </svg>
    <div class="inner" aria-hidden="true">
      <b class="num">{{ value == null ? '-' : Math.round(shown) }}</b>
      <span>{{ label }}</span>
    </div>
  </div>
</template>

<style scoped>
.ring { position: relative; flex: none; }
svg { width: 100%; height: 100%; transform: rotate(-90deg); display: block; }
.track { fill: none; stroke: var(--color-border); stroke-width: 7; }
.fill { fill: none; stroke: var(--color-primary); stroke-width: 7; stroke-linecap: round; }
.inner { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; line-height: 1.05; }
.inner b { font-size: calc(var(--s) * .31px); font-weight: 800; color: var(--color-heading); }
.inner span { font-size: max(10px, calc(var(--s) * .11px)); color: var(--color-muted-foreground); font-weight: 600; }
</style>
