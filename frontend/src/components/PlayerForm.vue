<script setup>
// Player form (ANL-08): last N games against the earlier games of the season. Up / down only when the change is
// both clear (statistical test) and large enough to matter; the words and arrows say it, not only the colour.
// Interaction: point at (or arrow through) the sparkline to read each game; click or Enter opens that match.
// Motion: the line draws in when first seen and the "recent games" shading fades in after it.
import { computed, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { DURATION, ease, useInView, useTween } from '@/motion/core'
import { fmtDate } from '@/utils/format'

const props = defineProps({ form: { type: Object, required: true } }) // /api/players/{code} `form`
const router = useRouter()

const TREND = { up: '상승', down: '하락', flat: '변화 없음' }
const OVERALL = { up: '폼 상승', down: '폼 하락', mixed: '엇갈림', flat: '평소와 비슷' }
const fmt = (m, v) => (m.key === 'points_per_set' ? v.toFixed(2) : `${v.toFixed(1)}%`)
const sign = (v) => (v > 0 ? '+' : '')

// sparkline: points per set of every game, last N highlighted
const uid = useId()
const W = 300
const H = 64
const series = computed(() => props.form.series)
const pts = computed(() => series.value.map((s) => s.points_per_set))
const max = computed(() => Math.max(1, ...pts.value))
const x = (i) => (pts.value.length > 1 ? (i / (pts.value.length - 1)) * W : W / 2)
const y = (v) => H - 4 - (v / max.value) * (H - 8)
const line = computed(() => pts.value.map((v, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(v)}`).join(' '))
const splitX = computed(() => x(pts.value.length - props.form.n) - (W / Math.max(1, pts.value.length - 1)) / 2)
const avg = (arr) => arr.reduce((a, b) => a + b, 0) / Math.max(1, arr.length)
const beforeAvg = computed(() => avg(pts.value.slice(0, -props.form.n)))
const recentAvg = computed(() => avg(pts.value.slice(-props.form.n)))

// ---- pointed game
const active = ref(null)
const shown = computed(() => (active.value == null ? null : series.value[active.value]))
const isRecent = (i) => i >= pts.value.length - props.form.n
function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect()
  const n = pts.value.length
  active.value = Math.min(n - 1, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * (n - 1))))
}
function onKey(e) {
  const n = pts.value.length
  if (e.key === 'Enter' && shown.value?.match) { open(); return }
  const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -n, End: n }
  if (!(e.key in moves)) return
  e.preventDefault()
  active.value = Math.min(n - 1, Math.max(0, (active.value ?? (moves[e.key] > 0 ? -1 : n)) + moves[e.key]))
}
const open = () => { if (shown.value?.match) router.push(`/matches/${shown.value.match}`) }
const readoutLeft = computed(() => `${Math.min(80, Math.max(20, (x(active.value) / W) * 100))}%`)

// ---- draw-in once visible
const plot = ref(null)
const seen = useInView(plot)
const drawn = useTween(() => (seen.value ? 1 : 0), { duration: DURATION.chart, easing: ease.inOut, from: 0 })
</script>

<template>
  <div class="pf">
    <p class="head">
      <span class="badge" :class="form.overall">{{ OVERALL[form.overall] }}</span>
      <span class="muted small">최근 {{ form.n }}경기 vs 그 전 {{ form.games_before }}경기</span>
    </p>
    <ul class="metrics">
      <li v-for="m in form.metrics" :key="m.key">
        <span class="label small">{{ m.label }}</span>
        <span class="vals"><span class="muted">{{ fmt(m, m.before) }}</span> → <b>{{ fmt(m, m.recent) }}</b></span>
        <span class="trend small" :class="m.trend">
          {{ m.trend === 'up' ? '▲' : m.trend === 'down' ? '▼' : '–' }} {{ TREND[m.trend] }}
          <span class="muted">({{ sign(m.change) }}{{ m.key === 'points_per_set' ? m.change.toFixed(2) : `${m.change.toFixed(1)}%p` }})</span>
        </span>
      </li>
    </ul>
    <figure class="spark">
      <div ref="plot" class="plot" tabindex="0" role="img"
           :aria-label="`경기별 세트당 득점. 그 전 평균 ${beforeAvg.toFixed(2)}, 최근 ${form.n}경기 평균 ${recentAvg.toFixed(2)}. 좌우 화살표로 경기별 값, Enter로 그 경기 보기.`"
           @pointermove="onMove" @pointerleave="active = null" @click="open" @keydown="onKey" @blur="active = null">
        <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" aria-hidden="true">
          <defs><clipPath :id="`draw-${uid}`"><rect x="0" y="0" :width="W * drawn" :height="H" /></clipPath></defs>
          <rect :x="splitX" y="0" :width="W - splitX" :height="H" class="recent" :class="{ on: drawn === 1 }" />
          <line x1="0" :x2="splitX" :y1="y(beforeAvg)" :y2="y(beforeAvg)" class="avg" />
          <line :x1="splitX" :x2="W" :y1="y(recentAvg)" :y2="y(recentAvg)" class="avg now" />
          <path :d="line" class="line" :clip-path="`url(#draw-${uid})`" />
          <line v-if="shown" :x1="x(active)" :x2="x(active)" y1="0" :y2="H" class="cursor" />
        </svg>
        <span v-if="shown" class="dot" :class="{ recent: isRecent(active) }"
              :style="{ left: `${(x(active) / W) * 100}%`, top: `${(y(shown.points_per_set) / H) * 100}%` }" />
        <div v-if="shown" class="readout" :style="{ left: readoutLeft }">
          <b>{{ shown.points_per_set.toFixed(2) }}</b> <span>{{ fmtDate(shown.date) }}{{ isRecent(active) ? ' · 최근' : '' }}</span>
        </div>
      </div>
      <figcaption class="muted small">경기별 세트당 득점 · 음영은 최근 {{ form.n }}경기, 가로선은 구간 평균 · 점을 누르면 그 경기로 이동</figcaption>
    </figure>
    <p class="muted small note">우연으로 보기 어려운 차이(통계 검정)이면서 의미 있는 크기(세트당 0.5점, 성공률 5%p 이상)일 때만 상승·하락으로 표시합니다.</p>
  </div>
</template>

<style scoped>
.head { display: flex; gap: 8px; align-items: center; margin: 0 0 var(--space-2); flex-wrap: wrap; }
.badge.up { background: color-mix(in srgb, var(--color-good) 12%, transparent); color: var(--color-good); }
.badge.down { background: color-mix(in srgb, var(--color-bad) 10%, transparent); color: var(--color-bad); }
.badge.flat, .badge.mixed { background: var(--color-muted); color: var(--color-muted-foreground); }
.metrics { list-style: none; padding: 0; margin: 0; }
.metrics li { display: grid; grid-template-columns: 92px 1fr auto; gap: 8px; align-items: baseline; padding: 5px 0;
              border-bottom: 1px solid var(--color-border-soft); }
.label { color: var(--color-muted-foreground); }
.vals { font-variant-numeric: tabular-nums; }
.trend { font-weight: 700; white-space: nowrap; }
.trend.up { color: var(--color-good); }
.trend.down { color: var(--color-bad); }
.trend.flat { color: var(--color-muted-foreground); font-weight: 500; }
.spark { margin: var(--space-3) 0 0; }
.plot { position: relative; height: 64px; cursor: pointer; touch-action: pan-y; }
.plot svg { width: 100%; height: 64px; display: block; }
.recent { fill: var(--color-primary-soft); opacity: 0; transition: opacity var(--dur-slow) var(--ease-out); }
.recent.on { opacity: 1; }
.avg { stroke: var(--color-muted-foreground); stroke-dasharray: 3 3; stroke-width: 1; vector-effect: non-scaling-stroke; }
.avg.now { stroke: var(--color-primary); }
.line { fill: none; stroke: var(--color-foreground); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.cursor { stroke: var(--color-muted-foreground); stroke-width: 1; vector-effect: non-scaling-stroke; }
.dot { position: absolute; width: 8px; height: 8px; border-radius: 50%; transform: translate(-50%, -50%); pointer-events: none;
       background: var(--color-foreground); border: 2px solid var(--color-card); }
.dot.recent { background: var(--color-primary); }
.readout { position: absolute; bottom: calc(100% + 4px); transform: translateX(-50%); background: var(--color-foreground); color: #fff;
           font-size: .75rem; padding: 2px 8px; border-radius: 6px; white-space: nowrap; pointer-events: none; display: flex; gap: 6px; }
.readout span { opacity: .85; }
.note { margin: var(--space-2) 0 0; }
</style>
