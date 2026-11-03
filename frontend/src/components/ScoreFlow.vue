<script setup>
// Lead over one set, from play-by-play: above the centre line the home team leads, below the away team.
// Chart guidance (ui-ux-pro-max, "Trend Over Time"): hover/keyboard reveals the score at each point,
// and a sentence summary (lead changes, longest run) gives the same information without the chart.
import { computed, ref, useId } from 'vue'

const props = defineProps({
  events: { type: Array, required: true }, // rally events with grade, home_score, away_score
  home: { type: String, required: true },
  away: { type: String, required: true },
})

const uid = useId() // unique clip-path ids when several charts are on one page
const W = 600
const H = 170
const TOP = 10

// one point per scored rally
const points = computed(() => {
  const out = [{ h: 0, a: 0, by: null }]
  for (const e of props.events) {
    if (e.grade <= 0) continue
    const prev = out[out.length - 1]
    out.push({ h: e.home_score, a: e.away_score, by: e.home_score > prev.h ? 'home' : 'away' })
  }
  return out
})
const n = computed(() => points.value.length - 1)
const maxLead = computed(() => Math.max(4, ...points.value.map((p) => Math.abs(p.h - p.a))))
const mid = H / 2
const x = (i) => (i / Math.max(1, n.value)) * W
const y = (d) => mid - (d / maxLead.value) * (mid - TOP)
const yPct = (d) => `${(y(d) / H) * 100}%`

const path = computed(() => {
  const p = points.value
  let d = `M 0 ${y(0)}`
  for (let i = 1; i < p.length; i++) d += ` H ${x(i)} V ${y(p[i].h - p[i].a)}`
  return d
})
const area = computed(() => `${path.value} H ${W} V ${mid} H 0 Z`)
const grid = computed(() => [5, 10].filter((v) => v < maxLead.value))
const final = computed(() => points.value[points.value.length - 1])

const summary = computed(() => {
  const p = points.value
  let changes = 0
  let leader = 0
  let run = { len: 0, by: null, end: 0 }
  let cur = { len: 0, by: null }
  let biggest = { lead: 0, i: 0 }
  p.forEach((pt, i) => {
    const d = pt.h - pt.a
    const sign = Math.sign(d)
    if (sign && leader && sign !== leader) changes += 1
    if (sign) leader = sign
    if (Math.abs(d) > Math.abs(biggest.lead)) biggest = { lead: d, i }
    if (!pt.by) return
    cur = pt.by === cur.by ? { len: cur.len + 1, by: pt.by } : { len: 1, by: pt.by }
    if (cur.len > run.len) run = { ...cur, end: i }
  })
  const name = (side) => (side === 'home' ? props.home : props.away)
  const runFrom = p[run.end - run.len] || p[0]
  return {
    changes,
    biggest: biggest.lead ? `${name(biggest.lead > 0 ? 'home' : 'away')} ${Math.abs(biggest.lead)}점 차 (${p[biggest.i].h}:${p[biggest.i].a})` : '없음',
    run: run.len ? `${name(run.by)} ${run.len}연속 득점 (${runFrom.h}:${runFrom.a} → ${p[run.end].h}:${p[run.end].a})` : '없음',
  }
})

// readout: pointer position or arrow keys
const active = ref(null)
const shown = computed(() => (active.value == null ? null : points.value[active.value]))
function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect()
  active.value = Math.min(n.value, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * n.value)))
}
function onKey(e) {
  const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -n.value, End: n.value }
  if (!(e.key in moves)) return
  e.preventDefault()
  const base = active.value ?? (moves[e.key] > 0 ? 0 : n.value)
  active.value = Math.min(n.value, Math.max(0, base + moves[e.key]))
}
</script>

<template>
  <figure class="flow">
    <div class="plot" tabindex="0" role="img"
         :aria-label="`${home} 대 ${away} 점수 흐름. 최종 ${final.h} 대 ${final.a}, 리드 변경 ${summary.changes}회, 최다 연속 득점 ${summary.run}. 좌우 화살표로 득점마다 점수를 볼 수 있습니다.`"
         @pointermove="onMove" @pointerleave="active = null" @keydown="onKey" @blur="active = null">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <clipPath :id="`top-${uid}`"><rect x="0" y="0" :width="W" :height="mid" /></clipPath>
          <clipPath :id="`bottom-${uid}`"><rect x="0" :y="mid" :width="W" :height="H - mid" /></clipPath>
        </defs>
        <template v-for="g in grid" :key="g">
          <line x1="0" :x2="W" :y1="y(g)" :y2="y(g)" class="grid" />
          <line x1="0" :x2="W" :y1="y(-g)" :y2="y(-g)" class="grid" />
        </template>
        <path :d="area" class="area home" :clip-path="`url(#top-${uid})`" />
        <path :d="area" class="area away" :clip-path="`url(#bottom-${uid})`" />
        <line x1="0" :x2="W" :y1="mid" :y2="mid" class="axis" />
        <path :d="path" class="line" />
        <line v-if="shown" :x1="x(active)" :x2="x(active)" y1="0" :y2="H" class="cursor" />
      </svg>
      <span v-for="g in grid" :key="`u${g}`" class="tick" :style="{ top: yPct(g) }">+{{ g }}</span>
      <span v-for="g in grid" :key="`d${g}`" class="tick" :style="{ top: yPct(-g) }">+{{ g }}</span>
      <div v-if="shown" class="readout" :style="{ left: `${(x(active) / W) * 100}%` }">
        <b>{{ shown.h }}:{{ shown.a }}</b>
        <span v-if="shown.by">{{ shown.by === 'home' ? home : away }} 득점</span>
      </div>
    </div>
    <figcaption>
      <div class="legend small">
        <span class="home"><i class="sw home" />{{ home }} 리드</span>
        <span class="away"><i class="sw away" />{{ away }} 리드</span>
      </div>
      <dl class="facts small">
        <div><dt>리드 변경</dt><dd>{{ summary.changes }}회</dd></div>
        <div><dt>최다 연속 득점</dt><dd>{{ summary.run }}</dd></div>
        <div><dt>최대 점수 차</dt><dd>{{ summary.biggest }}</dd></div>
      </dl>
    </figcaption>
  </figure>
</template>

<style scoped>
.flow { margin: 0; }
.plot { position: relative; height: 170px; cursor: crosshair; touch-action: pan-y; border-radius: 6px; }
svg { width: 100%; height: 100%; display: block; }
.area.home { fill: color-mix(in srgb, var(--color-home) 16%, transparent); }
.area.away { fill: color-mix(in srgb, var(--color-away) 18%, transparent); }
.grid { stroke: var(--color-border); stroke-width: 1; stroke-dasharray: 3 4; vector-effect: non-scaling-stroke; }
.axis { stroke: var(--color-muted-foreground); stroke-width: 1; vector-effect: non-scaling-stroke; opacity: .5; }
.line { fill: none; stroke: var(--color-foreground); stroke-width: 1.6; vector-effect: non-scaling-stroke; }
.cursor { stroke: var(--color-muted-foreground); stroke-width: 1; vector-effect: non-scaling-stroke; }
.tick { position: absolute; left: 2px; transform: translateY(-50%); font-size: .7rem; color: var(--color-muted-foreground); pointer-events: none; }
.readout { position: absolute; top: 0; transform: translateX(-50%); background: var(--color-foreground); color: #fff; font-size: .78rem;
           padding: 2px 8px; border-radius: 6px; white-space: nowrap; pointer-events: none; display: flex; gap: 6px; }
.readout span { opacity: .85; }
.legend { display: flex; justify-content: space-between; gap: 8px; margin-top: 6px; font-weight: 600; }
.legend .home { color: var(--color-home); }
.legend .away { color: var(--color-away); }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.sw.home { background: color-mix(in srgb, var(--color-home) 40%, transparent); }
.sw.away { background: color-mix(in srgb, var(--color-away) 45%, transparent); }
.facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-2); margin: var(--space-3) 0 0; }
.facts div { background: var(--color-muted); border-radius: 8px; padding: 6px 10px; }
.facts dt { color: var(--color-muted-foreground); font-size: .75rem; }
.facts dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } }
</style>
