<script setup>
// Two values per row as opposing bars (home vs away, or team vs league average).
// Motion: bars grow out from the labels the first time they are seen and resize smoothly when the values change
// (e.g. switching set / whole match), using transform only.
import { ref } from 'vue'
import { useInView } from '@/motion/core'

const el = ref(null)
const seen = useInView(el)
defineProps({
  rows: { type: Array, required: true }, // [{ label, a, b, fmt?: (v) => string, lowerIsBetter?: bool }]
  aLabel: { type: String, required: true },
  bLabel: { type: String, required: true },
  bMuted: Boolean, // grey right side (e.g. league average)
})

const scale = (v, a, b) => {
  const max = Math.max(Math.abs(a ?? 0), Math.abs(b ?? 0))
  if (!seen.value || !max) return 'scaleX(0)'
  return `scaleX(${Math.max(0.02, Math.abs(v ?? 0) / max)})`
}
const better = (r, side) => {
  if (r.a == null || r.b == null || r.a === r.b) return false
  const aWins = r.lowerIsBetter ? r.a < r.b : r.a > r.b
  return side === 'a' ? aWins : !aWins
}
const show = (r, v) => (v == null ? '-' : r.fmt ? r.fmt(v) : v)
</script>

<template>
  <div ref="el" class="compare">
    <div class="head small"><span class="a">{{ aLabel }}</span><span class="b" :class="{ muted: bMuted }">{{ bLabel }}</span></div>
    <div v-for="(r, i) in rows" :key="r.label" class="row">
      <span class="val" :class="{ strong: better(r, 'a') }">{{ show(r, r.a) }}</span>
      <div class="bar left"><i :style="{ transform: scale(r.a, r.a, r.b), transitionDelay: `${i * 40}ms` }" /></div>
      <span class="label small">{{ r.label }}</span>
      <div class="bar right" :class="{ grey: bMuted }"><i :style="{ transform: scale(r.b, r.a, r.b), transitionDelay: `${i * 40}ms` }" /></div>
      <span class="val" :class="{ strong: better(r, 'b') }">{{ show(r, r.b) }}</span>
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 6px; }
.head .a { color: var(--color-home); }
.head .b { color: var(--color-away); }
.head .b.muted { color: var(--color-muted-foreground); }
.row { display: grid; grid-template-columns: 52px 1fr 104px 1fr 52px; align-items: center; gap: 6px; padding: 4px 0; }
.val { font-variant-numeric: tabular-nums; font-size: .9rem; }
.row .val:first-child { text-align: left; }
.row .val:last-child { text-align: right; }
.val.strong { font-weight: 800; }
.label { text-align: center; color: var(--color-muted-foreground); }
.bar { height: 8px; background: var(--color-border-soft); border-radius: 4px; display: flex; overflow: hidden; }
.bar.left i { background: var(--color-home); transform-origin: right center; }
.bar.right i { background: var(--color-away); }
.bar.right.grey i { background: var(--color-muted-foreground); }
.bar i { display: block; width: 100%; height: 100%; border-radius: 4px; transform-origin: left center;
         transition: transform var(--dur-slow) var(--ease-out); }
@media (max-width: 480px) { .row { grid-template-columns: 44px 1fr 84px 1fr 44px; } }
</style>
