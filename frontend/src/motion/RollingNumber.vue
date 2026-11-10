<script setup>
// 점수가 바뀌면 기계식 계수기처럼 굴러가는 스코어보드 숫자(주행 거리계 방식을 직접 구현: 자리마다 0~9가 세로로
// 늘어선 띠를 CSS transform으로 올리고 내리므로, 바뀐 자리만 움직인다).
// roll-in: 처음 그려질 때 0에서 값까지 굴러감(끝난 경기의 최종 점수 등).
// 숫자 띠는 화면 낭독기에서 숨기고, 숫자 자체는 한 번만 읽힌다. min-digits: 앞을 0으로 채울 최소 자릿수.
import { computed, onMounted, ref, watch } from 'vue'
import { reducedMotion } from './core'

const props = defineProps({
  value: { type: Number, required: true },
  minDigits: { type: Number, default: 1 },
  rollIn: Boolean,
})

const shown = ref(props.rollIn && !reducedMotion.value ? 0 : props.value)
watch(() => props.value, (v) => { shown.value = v })
// 첫 화면을 그린 뒤(두 프레임 뒤) 값을 바꿔야 CSS 전환이 실제로 일어난다
onMounted(() => {
  if (shown.value !== props.value) requestAnimationFrame(() => requestAnimationFrame(() => { shown.value = props.value }))
})

// 자리(일, 십, ...)를 키로 쓴다: 9가 10이 될 때도 일의 자리 띠가 같은 요소로 남아 자연스럽게 굴러가도록.
// lead: 값보다 앞에 채운 0(흐리게 그림)
const places = computed(() => {
  const digits = String(Math.max(0, Math.round(props.value))).length
  const s = String(Math.max(0, Math.round(shown.value))).padStart(Math.max(props.minDigits, digits), '0')
  return s.split('').map((d, i) => ({ place: s.length - 1 - i, digit: Number(d), lead: i < s.length - digits }))
})
</script>

<template>
  <span class="roll"><span class="sr-only">{{ value }}</span><span
    v-for="p in places" v-show="!p.lead || minDigits > 1" :key="p.place" class="col" aria-hidden="true"><span class="strip"
    :style="{ transform: `translateY(${-p.digit * 10}%)` }"><span v-for="d in 10" :key="d">{{ d - 1 }}</span></span></span></span>
</template>

<style scoped>
.roll { display: inline-flex; font-variant-numeric: tabular-nums; line-height: 1; }
.col { display: inline-block; height: 1.1em; overflow: hidden;
       -webkit-mask-image: linear-gradient(transparent, #000 12%, #000 88%, transparent);
       mask-image: linear-gradient(transparent, #000 12%, #000 88%, transparent); }
.strip { display: flex; flex-direction: column; transition: transform var(--dur-slow) var(--ease-out); }
.strip > span { height: 1.1em; line-height: 1.1em; text-align: center; }
</style>
