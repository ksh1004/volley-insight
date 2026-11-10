<script setup>
// 한 줄에 두 값을 마주 보는 막대로 비교(홈 vs 원정, 또는 팀 vs 리그 평균). 더 나은 쪽은 굵게 표시.
// 모션: 처음 보일 때 막대가 이름표 쪽에서 자라나고, 값이 바뀌면(세트 ↔ 경기 전체 전환 등) 부드럽게 길이가 바뀐다.
// transform(scaleX)만 써서 레이아웃을 다시 계산하지 않는다(가볍고 부드러움).
import { ref } from 'vue'
import { useInView } from '@/motion/core'

const el = ref(null)
const seen = useInView(el)
defineProps({
  rows: { type: Array, required: true }, // [{ label, a, b, fmt?: (v) => string, lowerIsBetter?: bool }]
  aLabel: { type: String, required: true },
  bLabel: { type: String, required: true },
  bMuted: Boolean, // 오른쪽을 회색으로(예: 리그 평균일 때)
})

// 막대 길이: 두 값 중 큰 쪽을 1로 본 비율(아직 안 보였으면 0, 아주 작은 값도 최소 2%는 보이게)
const scale = (v, a, b) => {
  const max = Math.max(Math.abs(a ?? 0), Math.abs(b ?? 0))
  if (!seen.value || !max) return 'scaleX(0)'
  return `scaleX(${Math.max(0.02, Math.abs(v ?? 0) / max)})`
}
// 이 쪽(side)이 더 나은가(lowerIsBetter면 작은 쪽이 나음). 같으면 둘 다 아님
const better = (r, side) => {
  if (r.a == null || r.b == null || r.a === r.b) return false
  const aWins = r.lowerIsBetter ? r.a < r.b : r.a > r.b
  return side === 'a' ? aWins : !aWins
}
// 값 표시(행마다 형식 함수가 있으면 그것으로)
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
