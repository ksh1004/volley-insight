<script setup>
// 새 값으로 부드럽게 미끄러지는 숫자(count-up이면 처음 화면에 보일 때 0부터 세어 올라감).
// 화면 낭독기에는 최종 값만 읽히고, 움직이는 숫자는 숨긴다(aria-hidden).
// format: 숫자를 글자로 바꾸는 함수(기본은 정수 반올림). 예: :format="(v) => v.toFixed(1) + '%'"
import { computed, ref, watch } from 'vue'
import { DURATION, useInView, useTween } from './core'

const props = defineProps({
  value: { type: Number, default: null },
  format: { type: Function, default: (v) => String(Math.round(v)) },
  duration: { type: Number, default: DURATION.number },
  countUp: Boolean,
})

const el = ref(null)
const shown = useTween(() => props.value ?? 0, { duration: props.duration, from: props.countUp ? 0 : undefined })
const visible = useInView(el) // 처음 화면에 보이면 count-up 시작
watch(visible, (v) => { if (v && props.countUp) shown.play(0) }, { immediate: true })

const finalText = computed(() => (props.value == null ? '-' : props.format(props.value)))
const text = computed(() => (props.value == null ? '-' : props.format(shown.value)))
</script>

<template>
  <span ref="el" class="anum"><span aria-hidden="true">{{ text }}</span><span class="sr-only">{{ finalText }}</span></span>
</template>

<style scoped>
.anum { font-variant-numeric: tabular-nums; }
</style>
