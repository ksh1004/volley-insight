<script setup>
// Team strengths and weaknesses as a radar (ANL-02, WEB-06). Each axis is the team's score among the league's teams
// (100 = best, 0 = worst); the dashed ring is the middle (50). Chart guidance (ui-ux-pro-max "Multi-Variable
// Comparison"): at most 8 axes, one dataset at 20% fill, the reference drawn with a different line style, every axis
// labelled with its rank, and the same facts as text (strengths / weaknesses) plus the value table on the page.
// Interaction: pointing at (or focusing) an axis label highlights that spoke and writes its rank and score.
// Motion: the shape grows out from the centre when first seen and morphs to the new shape when the data changes.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { DURATION, ease, reducedMotion, useInView, useTween } from '@/motion/core'

const props = defineProps({
  axes: { type: Array, required: true }, // [{ key, label, score, rank, teams, value, league, lower_is_better }]
  team: { type: String, required: true },
})

const S = 320
const C = S / 2
const R = 112
const usable = computed(() => props.axes.filter((a) => a.score != null))
const angle = (i) => (Math.PI * 2 * i) / props.axes.length - Math.PI / 2
const pt = (i, v) => [C + Math.cos(angle(i)) * R * (v / 100), C + Math.sin(angle(i)) * R * (v / 100)]
const poly = (vals) => vals.map((v, i) => pt(i, v).join(',')).join(' ')

// ---- drawn scores: grow from the centre once seen, then glide between data sets
const el = ref(null)
const seen = useInView(el)
const grow = useTween(() => (seen.value ? 1 : 0), { duration: DURATION.chart, easing: ease.out, from: 0 })
const target = computed(() => props.axes.map((a) => a.score ?? 0))
const drawn = ref(target.value.slice())
let raf = 0
watch(target, (next) => {
  cancelAnimationFrame(raf)
  if (reducedMotion.value) { drawn.value = next.slice(); return }
  const start = drawn.value.slice()
  const t0 = performance.now()
  const step = (now) => {
    const p = Math.min(1, (now - t0) / DURATION.slow)
    drawn.value = next.map((v, i) => (start[i] ?? 0) + (v - (start[i] ?? 0)) * ease.inOut(p))
    if (p < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
})
onBeforeUnmount(() => cancelAnimationFrame(raf))
const value = (i) => (drawn.value[i] ?? 0) * grow.value
const shape = computed(() => poly(props.axes.map((_, i) => value(i))))

const rings = [25, 50, 75, 100]
const labelPos = (i) => {
  const [x, y] = pt(i, 128)
  const cos = Math.cos(angle(i))
  return { x, y, anchor: Math.abs(cos) < 0.2 ? 'middle' : cos > 0 ? 'start' : 'end' }
}

const sorted = computed(() => [...usable.value].sort((a, b) => b.score - a.score))
const strengths = computed(() => sorted.value.filter((a) => a.score >= 60).slice(0, 3))
const weaknesses = computed(() => sorted.value.filter((a) => a.score <= 40).slice(-3).reverse())
const rankText = (a) => (a.rank ? `${a.teams}팀 중 ${a.rank}위` : '기록 없음')
const summary = computed(() => {
  const s = strengths.value.map((a) => `${a.label} ${rankText(a)}`).join(', ') || '두드러진 강점 없음'
  const w = weaknesses.value.map((a) => `${a.label} ${rankText(a)}`).join(', ') || '두드러진 약점 없음'
  return `${props.team} 강약점. 강점: ${s}. 약점: ${w}.`
})

// ---- pointed axis
const active = ref(null)
const activeAxis = computed(() => (active.value == null ? null : props.axes[active.value]))
const indexOf = (a) => props.axes.findIndex((x) => x.key === a.key)
</script>

<template>
  <div ref="el" class="radar" :class="{ pointing: active != null }">
    <svg :viewBox="`-40 -14 ${S + 80} ${S + 28}`" role="img" :aria-label="summary">
      <polygon v-for="r in rings" :key="r" :points="poly(axes.map(() => r))" class="ring" :class="{ mid: r === 50 }" />
      <line v-for="(a, i) in axes" :key="a.key" :x1="C" :y1="C" :x2="pt(i, 100)[0]" :y2="pt(i, 100)[1]" class="spoke"
            :class="{ on: active === i }" />
      <polygon :points="shape" class="team" />
      <circle v-for="(a, i) in axes" :key="`d${a.key}`" :cx="pt(i, value(i))[0]" :cy="pt(i, value(i))[1]"
              :r="active === i ? 5.5 : 3.2" class="dot" :class="{ on: active === i }" />
      <text v-for="(a, i) in axes" :key="`l${a.key}`" :x="labelPos(i).x" :y="labelPos(i).y" :text-anchor="labelPos(i).anchor"
            class="label" :class="{ on: active === i, dim: active != null && active !== i }"
            @pointerenter="active = i" @pointerleave="active = null">
        <tspan :x="labelPos(i).x" dy="-0.2em">{{ a.label }}</tspan>
        <tspan :x="labelPos(i).x" dy="1.2em" class="rank">{{ a.rank ? `${a.rank}위/${a.teams}` : '-' }}</tspan>
      </text>
    </svg>
    <p class="readout small" aria-live="polite">
      <template v-if="activeAxis"><b>{{ activeAxis.label }}</b> · {{ rankText(activeAxis) }} · 리그 내 점수
        {{ activeAxis.score ?? '-' }}<span class="muted"> (100 = 리그 최고)</span></template>
      <span v-else class="muted">축 이름이나 아래 강점·약점에 마우스를 올리면 그 항목을 짚어 줍니다.</span>
    </p>
    <div class="legend small">
      <span><i class="sw team" />{{ team }}</span>
      <span><i class="sw mid" />리그 중간 (50)</span>
      <span class="muted">바깥일수록 리그에서 좋음 · 범실은 적을수록 바깥</span>
    </div>
    <dl class="facts small">
      <div><dt>강점</dt><dd>
        <template v-if="strengths.length"><button v-for="(a, k) in strengths" :key="a.key" type="button" class="chip"
          @pointerenter="active = indexOf(a)" @pointerleave="active = null" @focus="active = indexOf(a)" @blur="active = null">{{ a.label }} ({{ a.rank }}위){{ k < strengths.length - 1 ? ',' : '' }}</button></template>
        <template v-else>두드러진 강점 없음</template></dd></div>
      <div><dt>약점</dt><dd>
        <template v-if="weaknesses.length"><button v-for="(a, k) in weaknesses" :key="a.key" type="button" class="chip"
          @pointerenter="active = indexOf(a)" @pointerleave="active = null" @focus="active = indexOf(a)" @blur="active = null">{{ a.label }} ({{ a.rank }}위){{ k < weaknesses.length - 1 ? ',' : '' }}</button></template>
        <template v-else>두드러진 약점 없음</template></dd></div>
    </dl>
  </div>
</template>

<style scoped>
.radar svg { width: 100%; max-width: 420px; display: block; margin: 0 auto; overflow: visible; }
.ring { fill: none; stroke: var(--color-border); stroke-width: 1; }
.ring.mid { stroke: var(--color-muted-foreground); stroke-dasharray: 4 4; }
.spoke { stroke: var(--color-border); stroke-width: 1; transition: stroke var(--dur-fast) var(--ease-out); }
.spoke.on { stroke: var(--color-primary); stroke-width: 2; }
.team { fill: color-mix(in srgb, var(--color-primary) 20%, transparent); stroke: var(--color-primary); stroke-width: 2;
        stroke-linejoin: round; }
.dot { fill: var(--color-primary); transition: r var(--dur-fast) var(--ease-spring); }
.dot.on { stroke: var(--color-card); stroke-width: 2; }
.label { font-size: 12px; fill: var(--color-foreground); font-weight: 600; cursor: default;
         transition: opacity var(--dur-fast) var(--ease-out), fill var(--dur-fast) var(--ease-out); }
.label.on { fill: var(--color-primary); }
.label.dim { opacity: .45; }
.label .rank { font-size: 11px; fill: var(--color-muted-foreground); font-weight: 500; }
.readout { text-align: center; min-height: 1.5em; margin: 2px 0 0; font-variant-numeric: tabular-nums; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; justify-content: center; margin-top: 4px; }
.sw { display: inline-block; width: 14px; height: 0; margin-right: 6px; vertical-align: 3px; }
.sw.team { height: 10px; vertical-align: -1px; background: color-mix(in srgb, var(--color-primary) 20%, transparent);
           border: 2px solid var(--color-primary); border-radius: 2px; }
.sw.mid { border-top: 1.5px dashed var(--color-muted-foreground); }
.facts { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); margin: var(--space-3) 0 0; }
.facts div { background: var(--color-muted); border-radius: 8px; padding: 6px 10px; }
.facts dt { color: var(--color-muted-foreground); font-size: .75rem; }
.facts dd { margin: 0; font-weight: 600; }
.chip { all: unset; cursor: default; border-radius: 4px; margin-right: 4px; }
.chip:hover, .chip:focus-visible { color: var(--color-primary); text-decoration: underline; text-underline-offset: 3px; }
.chip:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
@media (hover: none) { .readout .muted { display: none; } }
@media (max-width: 560px) { .facts { grid-template-columns: 1fr; } }
</style>
