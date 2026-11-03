<script setup>
// Team Elo over one season (ANL-01). Chart rules from design-system/volley-insight/MASTER.md:
// hover / arrow keys reveal each match, a sentence summary carries the same information, postseason
// matches are marked by shape (hollow dot) as well as colour.
import { computed, ref } from 'vue'
import { fmtDate } from '@/utils/format'

const props = defineProps({
  history: { type: Array, required: true }, // [{ date, rating, change, opponent, result, score, postseason }]
  teamName: { type: String, required: true },
  rank: { type: Number, default: null }, // rank of the current rating in the league
  teams: { type: Number, default: null },
})

const W = 600
const H = 180
const PAD = { t: 12, b: 12 }
const pts = computed(() => props.history)
const n = computed(() => pts.value.length)
const lo = computed(() => Math.min(1500, ...pts.value.map((p) => p.rating)) - 15)
const hi = computed(() => Math.max(1500, ...pts.value.map((p) => p.rating)) + 15)
const x = (i) => (n.value <= 1 ? W / 2 : (i / (n.value - 1)) * W)
const y = (v) => PAD.t + (1 - (v - lo.value) / (hi.value - lo.value)) * (H - PAD.t - PAD.b)
const yPct = (v) => `${(y(v) / H) * 100}%`
const line = computed(() => pts.value.map((p, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(p.rating)}`).join(' '))

const summary = computed(() => {
  if (!n.value) return null
  const first = pts.value[0].rating - pts.value[0].change // rating before the first match
  const last = pts.value[n.value - 1].rating
  const peak = pts.value.reduce((a, b) => (b.rating > a.rating ? b : a))
  return { first, last, diff: last - first, peak }
})

const active = ref(null)
const shown = computed(() => (active.value == null ? null : pts.value[active.value]))
function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect()
  active.value = Math.min(n.value - 1, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * (n.value - 1))))
}
function onKey(e) {
  const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -n.value, End: n.value }
  if (!(e.key in moves)) return
  e.preventDefault()
  active.value = Math.min(n.value - 1, Math.max(0, (active.value ?? (moves[e.key] > 0 ? -1 : n.value)) + moves[e.key]))
}
const fmt = (v) => v.toFixed(0)
const sign = (v) => (v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1))
</script>

<template>
  <figure class="rating" v-if="n">
    <div class="plot" tabindex="0" role="img"
         :aria-label="`${teamName} 전력 지수 추이. 시즌 시작 ${fmt(summary.first)}에서 현재 ${fmt(summary.last)}, 최고 ${fmt(summary.peak.rating)}. 좌우 화살표로 경기별 값을 볼 수 있습니다.`"
         @pointermove="onMove" @pointerleave="active = null" @keydown="onKey" @blur="active = null">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" aria-hidden="true">
        <line x1="0" :x2="W" :y1="y(1500)" :y2="y(1500)" class="base" />
        <path :d="line" class="line" />
        <line v-if="shown" :x1="x(active)" :x2="x(active)" y1="0" :y2="H" class="cursor" />
      </svg>
      <span class="tick" :style="{ top: yPct(1500) }">1500 (리그 평균)</span>
      <span v-for="(p, i) in pts" :key="p.match" class="dot" :class="{ post: p.postseason, active: i === active, l: p.result === 'L' }"
            :style="{ left: `${(x(i) / W) * 100}%`, top: yPct(p.rating) }" />
      <div v-if="shown" class="readout" :style="{ left: `${Math.min(85, Math.max(15, (x(active) / W) * 100))}%` }">
        <b>{{ fmt(shown.rating) }}</b> <span :class="shown.change >= 0 ? 'up' : 'down'">{{ sign(shown.change) }}</span>
        <span class="muted-l">{{ fmtDate(shown.date) }} vs {{ shown.opponent }} {{ shown.result === 'W' ? '승' : '패' }} {{ shown.score }}</span>
      </div>
    </div>
    <figcaption class="small">
      <dl class="facts">
        <div><dt>현재</dt><dd>{{ fmt(summary.last) }} <span :class="summary.diff >= 0 ? 'up' : 'down'">({{ sign(summary.diff) }})</span></dd></div>
        <div><dt>시즌 최고</dt><dd>{{ fmt(summary.peak.rating) }} <span class="muted">{{ fmtDate(summary.peak.date) }}</span></dd></div>
        <div v-if="rank"><dt>리그 내 순위</dt><dd>{{ rank }}위 / {{ teams }}팀</dd></div>
      </dl>
      <p class="muted note">경기 결과로 계산한 Elo 점수예요. 강한 팀을 이기면 많이 오르고, 약한 팀에 지면 많이 내려가요. 속이 빈 점은 포스트시즌 경기.</p>
    </figcaption>
  </figure>
  <p v-else class="empty">이 시즌 전력 지수가 아직 없습니다.</p>
</template>

<style scoped>
.rating { margin: 0; }
.plot { position: relative; height: 180px; cursor: crosshair; touch-action: pan-y; }
svg { width: 100%; height: 100%; display: block; }
.base { stroke: var(--color-muted-foreground); stroke-dasharray: 4 4; stroke-width: 1; vector-effect: non-scaling-stroke; opacity: .6; }
.line { fill: none; stroke: var(--color-primary); stroke-width: 2; vector-effect: non-scaling-stroke; }
.cursor { stroke: var(--color-muted-foreground); stroke-width: 1; vector-effect: non-scaling-stroke; }
.tick { position: absolute; right: 0; transform: translateY(-120%); font-size: .7rem; color: var(--color-muted-foreground); }
.dot { position: absolute; width: 6px; height: 6px; margin: -3px 0 0 -3px; border-radius: 50%; background: var(--color-primary); pointer-events: none; }
.dot.l { background: var(--color-card); border: 1.5px solid var(--color-primary); }
.dot.post { width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px; background: var(--color-card); border: 2px solid var(--color-accent); }
.dot.active { transform: scale(1.6); }
.readout { position: absolute; top: 0; transform: translateX(-50%); background: var(--color-foreground); color: #fff; font-size: .78rem;
           padding: 3px 8px; border-radius: 6px; white-space: nowrap; pointer-events: none; display: flex; gap: 6px; align-items: baseline; }
.readout .muted-l { opacity: .8; }
.readout .up { color: #86efac; }
.readout .down { color: #fca5a5; }
.facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-2); margin: var(--space-3) 0 var(--space-2); }
.facts div { background: var(--color-muted); border-radius: 8px; padding: 6px 10px; }
.facts dt { color: var(--color-muted-foreground); font-size: .75rem; }
.facts dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; }
.up { color: var(--color-good); }
.down { color: var(--color-bad); }
.note { margin: 0; font-size: .8rem; }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } }
</style>
