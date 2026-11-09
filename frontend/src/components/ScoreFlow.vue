<script setup>
// Lead over one set, from play-by-play: above the centre line the home team leads, below the away team.
// Chart guidance (ui-ux-pro-max, "Trend Over Time"): hover/keyboard reveals the score at each point,
// and a sentence summary (lead changes, longest run) gives the same information without the chart.
// Interaction: the summary facts are linked to the chart; pointing at (or focusing) one marks it on the chart
// (the run as a band, the biggest lead as a ring, lead changes as ticks on the centre line).
// Motion: the line draws in from the left when first seen; during a live game new points extend it smoothly.
import { computed, ref, useId } from 'vue'
import { DURATION, ease, useInView, useTween } from '@/motion/core'

const props = defineProps({
  events: { type: Array, required: true }, // rally events with grade, home_score, away_score
  home: { type: String, required: true },
  away: { type: String, required: true },
  live: Boolean, // the set is still being played: mark the newest point
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

// scales follow the data smoothly: a new point squeezes the line instead of jumping
const span = useTween(() => Math.max(1, n.value), { duration: DURATION.slow, easing: ease.inOut })
const lead = useTween(() => maxLead.value, { duration: DURATION.slow, easing: ease.inOut })
const mid = H / 2
const x = (i) => (i / span.value) * W
const y = (d) => mid - (d / lead.value) * (mid - TOP)
const yPct = (d) => `${(y(d) / H) * 100}%`
const xPct = (i) => `${(x(i) / W) * 100}%`

const path = computed(() => {
  const p = points.value
  let d = `M 0 ${y(0)}`
  for (let i = 1; i < p.length; i++) d += ` H ${x(i)} V ${y(p[i].h - p[i].a)}`
  return d
})
const area = computed(() => `${path.value} H ${x(n.value)} V ${mid} H 0 Z`)
const grid = computed(() => [5, 10].filter((v) => v < maxLead.value))
const final = computed(() => points.value[points.value.length - 1])

const summary = computed(() => {
  const p = points.value
  const changeAt = []
  let leader = 0
  let run = { len: 0, by: null, end: 0 }
  let cur = { len: 0, by: null }
  let biggest = { lead: 0, i: 0 }
  p.forEach((pt, i) => {
    const d = pt.h - pt.a
    const sign = Math.sign(d)
    if (sign && leader && sign !== leader) changeAt.push(i)
    if (sign) leader = sign
    if (Math.abs(d) > Math.abs(biggest.lead)) biggest = { lead: d, i }
    if (!pt.by) return
    cur = pt.by === cur.by ? { len: cur.len + 1, by: pt.by } : { len: 1, by: pt.by }
    if (cur.len > run.len) run = { ...cur, end: i }
  })
  const name = (side) => (side === 'home' ? props.home : props.away)
  const runFrom = p[run.end - run.len] || p[0]
  return {
    changes: changeAt.length,
    changeAt,
    biggestAt: biggest.lead ? biggest.i : null,
    runRange: run.len ? [run.end - run.len, run.end] : null,
    runSide: run.by,
    biggest: biggest.lead ? `${name(biggest.lead > 0 ? 'home' : 'away')} ${Math.abs(biggest.lead)}점 차 (${p[biggest.i].h}:${p[biggest.i].a})` : '없음',
    run: run.len ? `${name(run.by)} ${run.len}연속 득점 (${runFrom.h}:${runFrom.a} → ${p[run.end].h}:${p[run.end].a})` : '없음',
  }
})

// readout: pointer position or arrow keys
const active = ref(null)
const shown = computed(() => (active.value == null ? null : points.value[active.value]))
function onMove(e) {
  const r = e.currentTarget.getBoundingClientRect()
  active.value = Math.min(n.value, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * span.value)))
}
function onKey(e) {
  const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -n.value, End: n.value }
  if (!(e.key in moves)) return
  e.preventDefault()
  const base = active.value ?? (moves[e.key] > 0 ? 0 : n.value)
  active.value = Math.min(n.value, Math.max(0, base + moves[e.key]))
}
// the readout stays inside the chart near both edges
const readoutLeft = computed(() => `${Math.min(88, Math.max(12, (x(active.value) / W) * 100))}%`)

// which summary fact is pointed at: run | biggest | changes
const focus = ref(null)

// draw-in once visible
const plot = ref(null)
const seen = useInView(plot)
const drawn = useTween(() => (seen.value ? 1 : 0), { duration: DURATION.chart, easing: ease.inOut, from: 0 })
</script>

<template>
  <figure class="flow" :class="{ focusing: focus }">
    <div ref="plot" class="plot" tabindex="0" role="img"
         :aria-label="`${home} 대 ${away} 점수 흐름. 최종 ${final.h} 대 ${final.a}, 리드 변경 ${summary.changes}회, 최다 연속 득점 ${summary.run}. 좌우 화살표로 득점마다 점수를 볼 수 있습니다.`"
         @pointermove="onMove" @pointerleave="active = null" @keydown="onKey" @blur="active = null">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <clipPath :id="`top-${uid}`"><rect x="0" y="0" :width="W" :height="mid" /></clipPath>
          <clipPath :id="`bottom-${uid}`"><rect x="0" :y="mid" :width="W" :height="H - mid" /></clipPath>
          <clipPath :id="`draw-${uid}`"><rect x="0" y="0" :width="W * drawn" :height="H" /></clipPath>
        </defs>
        <template v-for="g in grid" :key="g">
          <line x1="0" :x2="W" :y1="y(g)" :y2="y(g)" class="grid" />
          <line x1="0" :x2="W" :y1="y(-g)" :y2="y(-g)" class="grid" />
        </template>
        <rect v-if="focus === 'run' && summary.runRange" class="band" :class="summary.runSide"
              :x="x(summary.runRange[0])" y="0" :width="x(summary.runRange[1]) - x(summary.runRange[0])" :height="H" />
        <g :clip-path="`url(#draw-${uid})`">
          <path :d="area" class="area home" :clip-path="`url(#top-${uid})`" />
          <path :d="area" class="area away" :clip-path="`url(#bottom-${uid})`" />
          <path :d="path" class="line" />
        </g>
        <line x1="0" :x2="W" :y1="mid" :y2="mid" class="axis" />
        <line v-if="shown" :x1="x(active)" :x2="x(active)" y1="0" :y2="H" class="cursor" />
      </svg>
      <span v-for="g in grid" :key="`u${g}`" class="tick" :style="{ top: yPct(g) }">+{{ g }}</span>
      <span v-for="g in grid" :key="`d${g}`" class="tick" :style="{ top: yPct(-g) }">+{{ g }}</span>
      <!-- markers are HTML so they stay round on a stretched chart -->
      <template v-if="focus === 'changes'">
        <span v-for="i in summary.changeAt" :key="`c${i}`" class="mark change" :style="{ left: xPct(i), top: yPct(0) }" />
      </template>
      <span v-if="focus === 'biggest' && summary.biggestAt != null" class="mark ring"
            :style="{ left: xPct(summary.biggestAt), top: yPct(points[summary.biggestAt].h - points[summary.biggestAt].a) }" />
      <span v-if="live && n && drawn === 1" :key="`end${n}`" class="mark end" :class="final.by"
            :style="{ left: xPct(n), top: yPct(final.h - final.a) }" />
      <span v-if="shown" class="mark dot" :class="shown.by" :style="{ left: xPct(active), top: yPct(shown.h - shown.a) }" />
      <div v-if="shown" class="readout" :style="{ left: readoutLeft }">
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
        <div tabindex="0" :class="{ on: focus === 'changes' }" @pointerenter="focus = 'changes'" @pointerleave="focus = null"
             @focus="focus = 'changes'" @blur="focus = null"><dt>리드 변경</dt><dd>{{ summary.changes }}회</dd></div>
        <div tabindex="0" :class="{ on: focus === 'run' }" @pointerenter="focus = 'run'" @pointerleave="focus = null"
             @focus="focus = 'run'" @blur="focus = null"><dt>최다 연속 득점</dt><dd>{{ summary.run }}</dd></div>
        <div tabindex="0" :class="{ on: focus === 'biggest' }" @pointerenter="focus = 'biggest'" @pointerleave="focus = null"
             @focus="focus = 'biggest'" @blur="focus = null"><dt>최대 점수 차</dt><dd>{{ summary.biggest }}</dd></div>
      </dl>
      <p class="hint small">항목에 마우스를 올리거나 선택하면 차트에 표시됩니다.</p>
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
.band { opacity: .14; animation: fade-in var(--dur-base) var(--ease-out); }
.band.home { fill: var(--color-home); }
.band.away { fill: var(--color-away); }
.tick { position: absolute; left: 2px; transform: translateY(-50%); font-size: .7rem; color: var(--color-muted-foreground); pointer-events: none; }

.mark { position: absolute; pointer-events: none; border-radius: 50%; transform: translate(-50%, -50%); }
.mark.dot { width: 9px; height: 9px; background: var(--color-foreground); border: 2px solid var(--color-card); }
.mark.dot.home { background: var(--color-home); }
.mark.dot.away { background: var(--color-away); }
.mark.change { width: 8px; height: 8px; background: var(--color-card); border: 2px solid var(--color-foreground);
               animation: pop var(--dur-base) var(--ease-spring) both; }
.mark.ring { width: 18px; height: 18px; border: 2px solid var(--color-foreground); animation: pop var(--dur-base) var(--ease-spring) both; }
.mark.end { width: 8px; height: 8px; background: var(--color-foreground); }
.mark.end.home { background: var(--color-home); }
.mark.end.away { background: var(--color-away); }
/* the newest live point: one ripple each time it moves */
.mark.end::after { content: ''; position: absolute; inset: -2px; border-radius: 50%; border: 2px solid currentColor;
                   animation: ripple 900ms var(--ease-out) both; }
.mark.end.home::after { color: var(--color-home); }
.mark.end.away::after { color: var(--color-away); }

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
.facts div { background: var(--color-muted); border-radius: 8px; padding: 6px 10px; border: 1px solid transparent; cursor: default;
             transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out); }
.facts div.on { background: var(--color-primary-soft); border-color: var(--color-border-strong); }
.facts dt { color: var(--color-muted-foreground); font-size: .75rem; }
.facts dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.hint { color: var(--color-muted-foreground); margin: 6px 0 0; font-size: .75rem; }
@media (hover: none) { .hint { display: none; } }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } }

@keyframes pop { from { transform: translate(-50%, -50%) scale(.3); opacity: 0; } }
@keyframes ripple { from { transform: scale(1); opacity: .9; } to { transform: scale(3.2); opacity: 0; } }
@keyframes fade-in { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .mark.end::after { display: none; } }
</style>
