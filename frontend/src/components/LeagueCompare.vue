<script setup>
// Team value vs league average as a bar from the centre line: right = better than average, left = worse.
// Values close to the average (e.g. 51.2% vs 50.4%) stay readable, unlike two full-length bars.
// Motion: bars grow out of the centre line one after another when first seen; the difference label follows.
import { ref } from 'vue'
import { useInView } from '@/motion/core'

defineProps({
  rows: { type: Array, required: true }, // [{ label, a: team, b: league, fmt, lowerIsBetter }]
  scale: { type: Number, default: 25 }, // relative difference (%) that fills half the bar
})

const el = ref(null)
const seen = useInView(el)

const rel = (r) => {
  if (r.a == null || r.b == null || r.b === 0) return null
  const d = ((r.a - r.b) / Math.abs(r.b)) * 100
  return r.lowerIsBetter ? -d : d // positive = better
}
// the bar is half the track minus room for the label; its length is a scale of that, grown from the centre
const grow = (r, scale) => (seen.value ? Math.min(1, Math.abs(rel(r) ?? 0) / scale) : 0)
const show = (r, v) => (v == null ? '-' : r.fmt ? r.fmt(v) : v)
const sign = (v) => (v > 0 ? '+' : '')
</script>

<template>
  <div ref="el" class="lc" :class="{ seen }">
    <div class="lc-head small muted"><span>항목</span><span>팀 / 리그 평균</span><span class="center">← 평균보다 나쁨 · 좋음 →</span></div>
    <div v-for="(r, i) in rows" :key="r.label" class="lc-row" :style="{ '--d': `${i * 50}ms` }">
      <span class="label small">{{ r.label }}</span>
      <span class="vals"><b>{{ show(r, r.a) }}</b> <span class="muted small">/ {{ show(r, r.b) }}</span></span>
      <div class="track" :title="rel(r) == null ? '' : `리그 평균 대비 ${sign(rel(r))}${rel(r).toFixed(1)}%`">
        <i :class="rel(r) >= 0 ? 'good' : 'bad'" :style="{ transform: `scaleX(${grow(r, scale)})` }" />
        <span class="pct small" :class="rel(r) >= 0 ? 'good' : 'bad'" v-if="rel(r) != null">{{ sign(rel(r)) }}{{ rel(r).toFixed(0) }}%</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lc-head, .lc-row { display: grid; grid-template-columns: 96px 128px 1fr; gap: 10px; align-items: center; }
.lc-head { margin-bottom: 4px; }
.lc-head .center { text-align: center; }
.lc-row { padding: 5px 4px; margin: 0 -4px; border-bottom: 1px solid var(--color-border-soft); border-radius: 6px;
          transition: background-color var(--dur-fast) var(--ease-out); }
.lc-row:hover { background: var(--color-primary-soft); }
.label { color: var(--color-muted-foreground); }
.vals { font-variant-numeric: tabular-nums; white-space: nowrap; }
.track { position: relative; height: 18px; }
.track::before { content: ''; position: absolute; left: 50%; top: 0; bottom: 0; border-left: 1px solid var(--color-border); }
.track i { position: absolute; top: 5px; height: 8px; width: calc(50% - 40px); border-radius: 4px;
           transition: transform var(--dur-chart) var(--ease-out) var(--d, 0ms); }
.track i.good { left: 50%; background: var(--color-good); transform-origin: left center; }
.track i.bad { right: 50%; background: var(--color-bad); transform-origin: right center; }
.pct { position: absolute; top: 0; font-weight: 700; opacity: 0; transition: opacity var(--dur-base) linear calc(var(--d, 0ms) + 300ms); }
.seen .pct { opacity: 1; }
.pct.good { right: 0; color: var(--color-good); }
.pct.bad { left: 0; color: var(--color-bad); }
@media (max-width: 480px) { .lc-head, .lc-row { grid-template-columns: 80px 110px 1fr; } }
</style>
