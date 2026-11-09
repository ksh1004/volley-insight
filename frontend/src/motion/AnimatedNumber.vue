<script setup>
// A number that glides to each new value (and, with count-up, rises from 0 the first time it scrolls into view).
// Screen readers get only the final value; the moving digits are hidden from them.
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
const visible = useInView(el)
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
