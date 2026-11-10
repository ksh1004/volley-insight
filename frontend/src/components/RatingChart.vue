<script setup>
// 한 시즌의 팀 전력 지수(Elo) 추이(ANL-01). 차트 규칙(design-system/volley-insight/MASTER.md):
// 마우스·화살표 키로 경기마다 값을 읽을 수 있고, 같은 내용을 문장 요약으로도 주며, 포스트시즌 경기는 색과 함께
// 모양(속이 빈 점)으로도 구별한다.
// 상호작용(ui-ux-pro-max "시간에 따른 추세": 확대·되돌리기 버튼): 차트를 끌어 범위를 고르면 그 경기들로 확대,
// +/- 버튼(또는 키)으로 가리킨 경기 중심 확대·축소, '전체'로 시즌 전체 복귀.
// 시즌 초반(10경기 미만)에는 지난 시즌 마지막 경기들을 흐리게 앞에 붙이고, 시즌 사이 점수 조정(1500 쪽으로 3분의 1)을 점선으로 잇는다.
// 모션: 처음 보일 때 선이 그려지고, 확대할 때 가로·세로 축이 튀지 않고 미끄러지듯 바뀐다.
import { computed, ref, useId } from 'vue'
import { DURATION, ease, useInView, useTween } from '@/motion/core'
import { fmtDate } from '@/utils/format'

const props = defineProps({
  history: { type: Array, required: true }, // [{ date, rating, change, opponent, result, score, postseason }]
  teamName: { type: String, required: true },
  rank: { type: Number, default: null }, // 지금 점수의 리그 내 순위
  teams: { type: Number, default: null },
  // 시즌 초반: { season: { years }, history: [...] } = 지난 시즌 마지막 경기들(흐리게 그림)
  previous: { type: Object, default: null },
  seasonYears: { type: String, default: '' },
})

const uid = useId()
const W = 600
const H = 180
const PAD = { t: 18, b: 12 } // 위·아래 여백
const MIN_SPAN = 4 // 확대해도 최소 5경기는 보이게
const cur = computed(() => props.history)
const prev = computed(() => (cur.value.length && props.previous?.history?.length ? props.previous.history : []))
const offset = computed(() => prev.value.length) // 이번 시즌 첫 경기의 인덱스(앞에 지난 시즌 점들이 있으므로)
const pts = computed(() => [...prev.value.map((p) => ({ ...p, prev: true })), ...cur.value])
const n = computed(() => pts.value.length)
// 이번 시즌 첫 경기 전 점수(지난 시즌 점수를 1500 쪽으로 3분의 1 되돌린 값) = 첫 경기 점수 - 첫 경기 변화량
const startRating = computed(() => (cur.value.length ? cur.value[0].rating - cur.value[0].change : null))

// ---- 보이는 범위 [from, to](경기 인덱스). 확대할 때 부드럽게 바뀐다
const range = ref(null) // null = 시즌 전체
// 지난 시즌을 함께 그릴 때는 오른쪽에 여유를 둔다(마지막 점과 이름표가 가장자리에 붙지 않게)
const padEnd = computed(() => (offset.value ? 1.5 : 0))
const target = computed(() => range.value ?? [0, Math.max(1, n.value - 1) + padEnd.value])
const from = useTween(() => target.value[0], { duration: DURATION.slow, easing: ease.inOut })
const to = useTween(() => target.value[1], { duration: DURATION.slow, easing: ease.inOut })
const zoomed = computed(() => range.value != null)

// 지금 범위 안의 경기인가. 세로 눈금은 보이는 점들(+1500 기준선)에 맞춰 위아래 15점 여유
const inRange = (i) => i >= Math.floor(target.value[0]) && i <= Math.ceil(target.value[1])
const yDomain = computed(() => {
  const vis = pts.value.filter((_, i) => inRange(i)).map((p) => p.rating)
  if (offset.value && inRange(offset.value)) vis.push(startRating.value)
  return [Math.min(1500, ...vis) - 15, Math.max(1500, ...vis) + 15]
})
const lo = useTween(() => yDomain.value[0], { duration: DURATION.slow, easing: ease.inOut })
const hi = useTween(() => yDomain.value[1], { duration: DURATION.slow, easing: ease.inOut })

const x = (i) => (n.value <= 1 ? W / 2 : ((i - from.value) / Math.max(0.0001, to.value - from.value)) * W)
const y = (v) => PAD.t + (1 - (v - lo.value) / (hi.value - lo.value)) * (H - PAD.t - PAD.b)
const yPct = (v) => `${(y(v) / H) * 100}%`
const xPct = (i) => `${(x(i) / W) * 100}%`
// 이번 시즌 선: (지난 시즌을 그릴 때는) 시즌 구분선의 시작 점수에서 출발해 경기마다 이어 간다
const line = computed(() => {
  const o = offset.value
  const start = o ? `M ${x(o - 0.5)} ${y(startRating.value)} L` : 'M'
  return start + cur.value.map((p, i) => ` ${x(o + i)} ${y(p.rating)}`).join(' L')
})
const prevLine = computed(() => prev.value.map((p, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(p.rating)}`).join(' '))
// 지난 시즌 마지막 점수 → 이번 시즌 시작 점수: 비시즌 동안 1500 쪽으로 조정된 만큼을 잇는 선
const carry = computed(() => (offset.value ? { x1: x(offset.value - 1), y1: y(prev.value[offset.value - 1].rating),
                                                  x2: x(offset.value - 0.5), y2: y(startRating.value) } : null))

// 요약: 시즌 시작·현재 점수, 변화량, 최고점 경기
const summary = computed(() => {
  if (!cur.value.length) return null
  const c = cur.value
  const first = startRating.value // 첫 경기 전 점수
  const last = c[c.length - 1].rating
  const peak = c.reduce((a, b) => (b.rating > a.rating ? b : a))
  return { first, last, diff: last - first, peak }
})

// ---- 포인터: 올리면 그 경기 값을 읽고, 끌면 범위를 고른다
const active = ref(null)
const shown = computed(() => (active.value == null ? null : pts.value[active.value]))
const drag = ref(null) // 끄는 중인 범위 { start, end }(경기 인덱스)
// 마우스 위치 → 가장 가까운 경기 인덱스
const indexAt = (e, el) => {
  const r = el.getBoundingClientRect()
  const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
  return Math.min(n.value - 1, Math.max(0, Math.round(from.value + f * (to.value - from.value))))
}
// 누르기 시작: 경기가 충분히 많을 때만 범위 선택 시작(포인터를 붙잡아 차트 밖으로 나가도 계속 받음)
function onDown(e) {
  if (e.button !== 0 || n.value < MIN_SPAN + 2) return
  e.currentTarget.setPointerCapture?.(e.pointerId)
  const i = indexAt(e, e.currentTarget)
  drag.value = { start: i, end: i }
}
function onMove(e) {
  const i = indexAt(e, e.currentTarget)
  active.value = i
  if (drag.value) drag.value.end = i
}
function onUp() {
  const d = drag.value
  drag.value = null
  if (!d || Math.abs(d.end - d.start) < 2) return // 끈 게 아니라 클릭이었다
  zoomTo(Math.min(d.start, d.end), Math.max(d.start, d.end))
}
// [a, b] 범위로 확대(최소 범위 보장, 끝을 넘지 않게. 전체와 같으면 null)
function zoomTo(a, b) {
  if (b - a < MIN_SPAN) {
    const c = (a + b) / 2
    a = Math.round(c - MIN_SPAN / 2)
    b = a + MIN_SPAN
  }
  a = Math.max(0, a)
  b = Math.min(n.value - 1, b)
  range.value = a <= 0 && b >= n.value - 1 ? null : [a, b]
}
// 가리킨 경기(없으면 가운데)를 중심으로 factor배(0.5 = 확대, 2 = 축소)
function zoom(factor) {
  const [a, b] = target.value
  const centre = active.value ?? (a + b) / 2
  const half = ((b - a) * factor) / 2
  zoomTo(Math.round(centre - half), Math.round(centre + half))
}
const reset = () => { range.value = null }
// 키보드: ←→ 경기 이동, Home/End, + 확대, - 축소, 0 전체
function onKey(e) {
  const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -n.value, End: n.value }
  if (e.key === '+' || e.key === '=') { e.preventDefault(); zoom(0.5); return }
  if (e.key === '-') { e.preventDefault(); zoom(2); return }
  if (e.key === '0') { e.preventDefault(); reset(); return }
  if (!(e.key in moves)) return
  e.preventDefault()
  const [a, b] = [Math.ceil(target.value[0]), Math.min(n.value - 1, Math.floor(target.value[1]))]
  const next = (active.value ?? (moves[e.key] > 0 ? a - 1 : b + 1)) + moves[e.key]
  active.value = Math.min(b, Math.max(a, next))
}
// 끄는 동안 보이는 선택 영역 사각형
const brush = computed(() => {
  if (!drag.value) return null
  const [a, b] = [Math.min(drag.value.start, drag.value.end), Math.max(drag.value.start, drag.value.end)]
  return { left: xPct(a), width: `${((x(b) - x(a)) / W) * 100}%` }
})
// 지금 범위 설명("시즌 전체" 또는 "날짜 ~ 날짜 (N경기)")
const rangeText = computed(() => {
  if (!zoomed.value) return '시즌 전체'
  const [a, b] = target.value
  return `${fmtDate(pts.value[a].date)} ~ ${fmtDate(pts.value[b].date)} (${b - a + 1}경기)`
})

// ---- 처음 보일 때 선 그리기
const plot = ref(null)
const seen = useInView(plot)
const drawn = useTween(() => (seen.value ? 1 : 0), { duration: DURATION.chart, easing: ease.inOut, from: 0 })

const fmt = (v) => v.toFixed(0)
const sign = (v) => (v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1))
</script>

<template>
  <figure class="rating" v-if="cur.length">
    <div class="toolbar small">
      <span class="muted" aria-live="polite">{{ rangeText }}</span>
      <span class="zoom" role="group" aria-label="차트 확대">
        <button type="button" class="zbtn" :disabled="n < MIN_SPAN + 2 || (target[1] - target[0]) <= MIN_SPAN"
                aria-label="확대" @click="zoom(0.5)">+</button>
        <button type="button" class="zbtn" :disabled="!zoomed" aria-label="축소" @click="zoom(2)">−</button>
        <button type="button" class="zbtn wide" :disabled="!zoomed" @click="reset">전체</button>
      </span>
    </div>
    <div ref="plot" class="plot" :class="{ dragging: drag }" tabindex="0" role="img"
         :aria-label="`${teamName} 전력 지수 추이. 시즌 시작 ${fmt(summary.first)}에서 현재 ${fmt(summary.last)}, 최고 ${fmt(summary.peak.rating)}. 좌우 화살표로 경기별 값, 더하기·빼기 키로 확대·축소, 0 키로 전체 보기.`"
         @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="drag = null"
         @pointerleave="active = drag ? active : null" @dblclick="reset" @keydown="onKey" @blur="active = null">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" aria-hidden="true">
        <defs><clipPath :id="`draw-${uid}`"><rect x="0" y="0" :width="W * drawn" :height="H" /></clipPath></defs>
        <line x1="0" :x2="W" :y1="y(1500)" :y2="y(1500)" class="base" />
        <template v-if="offset">
          <line :x1="x(offset - 0.5)" :x2="x(offset - 0.5)" y1="0" :y2="H" class="divider" />
          <g :clip-path="`url(#draw-${uid})`">
            <path :d="prevLine" class="line prev" />
            <line v-bind="carry" class="carry" />
          </g>
        </template>
        <path :d="line" class="line" :clip-path="`url(#draw-${uid})`" />
        <line v-if="shown" :x1="x(active)" :x2="x(active)" y1="0" :y2="H" class="cursor" />
      </svg>
      <span class="tick" :style="{ top: yPct(1500) }">1500 (리그 평균)</span>
      <template v-if="offset && x(offset - 0.5) >= 0 && x(offset - 0.5) <= W">
        <span class="season-tag prev" :style="{ right: `${100 - (x(offset - 0.5) / W) * 100}%` }">
          {{ previous.season.years.replace('-', '–') }}</span>
        <span class="season-tag" :style="{ left: `${(x(offset - 0.5) / W) * 100}%` }">{{ seasonYears.replace('-', '–') || '이번 시즌' }}</span>
      </template>
      <template v-for="(p, i) in pts" :key="p.match">
        <span v-if="inRange(i) && x(i) <= W * drawn + 0.5" class="dot"
              :class="{ post: p.postseason, active: i === active, l: p.result === 'L', prev: p.prev }"
              :style="{ left: xPct(i), top: yPct(p.rating) }" />
      </template>
      <div v-if="brush" class="brush" :style="brush" />
      <div v-if="shown && !drag" class="readout" :style="{ left: `${Math.min(85, Math.max(15, (x(active) / W) * 100))}%` }">
        <b>{{ fmt(shown.rating) }}</b> <span :class="shown.change >= 0 ? 'up' : 'down'">{{ sign(shown.change) }}</span>
        <span class="muted-l">{{ fmtDate(shown.date) }} vs {{ shown.opponent }} {{ shown.result === 'W' ? '승' : '패' }} {{ shown.score }}<template
          v-if="shown.prev"> · 지난 시즌</template></span>
      </div>
    </div>
    <figcaption class="small">
      <dl class="facts">
        <div><dt>현재</dt><dd>{{ fmt(summary.last) }} <span :class="summary.diff >= 0 ? 'up' : 'down'">({{ sign(summary.diff) }})</span></dd></div>
        <div><dt>시즌 최고</dt><dd>{{ fmt(summary.peak.rating) }} <span class="muted">{{ fmtDate(summary.peak.date) }}</span></dd></div>
        <div v-if="rank"><dt>리그 내 순위</dt><dd>{{ rank }}위 / {{ teams }}팀</dd></div>
      </dl>
      <p class="muted note">경기 결과로 계산한 Elo 점수예요. 강한 팀을 이기면 많이 오르고, 약한 팀에 지면 많이 내려가요. 속이 빈 점은 포스트시즌 경기.
        <span class="pointer-only">차트를 옆으로 끌면 그 구간을 확대하고, 두 번 누르면 전체로 돌아가요.</span></p>
      <p v-if="offset" class="muted note">시즌 초반이라 지난 시즌 마지막 {{ offset }}경기를 흐린 점선으로 함께 보여 줘요.
        새 시즌 첫 경기 전에는 점수를 리그 평균(1500) 쪽으로 1/3 되돌려요(선수단이 바뀌기 때문): {{ fmt(prev[offset - 1].rating) }} → {{ fmt(startRating) }}.</p>
    </figcaption>
  </figure>
  <p v-else class="empty">이 시즌 전력 지수가 아직 없습니다.</p>
</template>

<style scoped>
.rating { margin: 0; }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; }
.zoom { display: inline-flex; gap: 4px; }
.zbtn { min-width: 30px; height: 28px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card);
        color: var(--color-foreground); font: inherit; font-weight: 700; cursor: pointer;
        transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out); }
.zbtn:hover:not(:disabled) { border-color: var(--color-primary); color: var(--color-primary); }
.zbtn:disabled { opacity: .45; cursor: not-allowed; }
@media (pointer: coarse) { .zbtn { min-width: 44px; height: 44px; } }
.plot { position: relative; height: 180px; cursor: crosshair; touch-action: pan-y; overflow: hidden; user-select: none; }
.plot.dragging { cursor: col-resize; }
svg { width: 100%; height: 100%; display: block; }
.base { stroke: var(--color-muted-foreground); stroke-dasharray: 4 4; stroke-width: 1; vector-effect: non-scaling-stroke; opacity: .6; }
.line { fill: none; stroke: var(--color-primary); stroke-width: 2; vector-effect: non-scaling-stroke; }
.line.prev { stroke: var(--color-muted-foreground); stroke-dasharray: 5 4; stroke-width: 1.5; opacity: .7; }
.carry { stroke: var(--color-muted-foreground); stroke-dasharray: 2 3; stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.divider { stroke: var(--color-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
.season-tag { position: absolute; top: 0; font-size: .7rem; font-weight: 700; color: var(--color-primary); padding: 0 6px; white-space: nowrap; }
.season-tag.prev { color: var(--color-muted-foreground); font-weight: 500; }
.dot.prev { background: var(--color-muted-foreground); opacity: .7; }
.dot.prev.l { background: var(--color-card); border-color: var(--color-muted-foreground); }
.cursor { stroke: var(--color-muted-foreground); stroke-width: 1; vector-effect: non-scaling-stroke; }
.tick { position: absolute; right: 0; transform: translateY(-120%); font-size: .7rem; color: var(--color-muted-foreground); }
.dot { position: absolute; width: 6px; height: 6px; margin: -3px 0 0 -3px; border-radius: 50%; background: var(--color-primary); pointer-events: none;
       transition: transform var(--dur-fast) var(--ease-spring); }
.dot.l { background: var(--color-card); border: 1.5px solid var(--color-primary); }
.dot.post { width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px; background: var(--color-card); border: 2px solid var(--color-accent); }
.dot.active { transform: scale(1.8); }
.brush { position: absolute; top: 0; bottom: 0; background: color-mix(in srgb, var(--color-primary) 12%, transparent);
         border-left: 1px solid var(--color-primary); border-right: 1px solid var(--color-primary); pointer-events: none; }
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
@media (hover: none) { .pointer-only { display: none; } }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } }
</style>
