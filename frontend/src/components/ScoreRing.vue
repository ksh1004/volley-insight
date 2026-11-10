<script setup>
// 1~99 점수를 원형 게이지로: 처음 화면에 보일 때 원이 차오르고 가운데 숫자도 함께 세어 올라간다.
// 원은 숫자를 반복해 보여 줄 뿐이고, 화면 낭독기에는 "종합 72점 (99점 만점)"처럼 한 번만 읽힌다.
// SVG 원의 pathLength를 100으로 두고 stroke-dasharray "점수 100"으로 채운 길이를 정한다.
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
const R = 42 // 원의 반지름(SVG 좌표 100 기준)
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
