<script setup>
// Scoreboard digits that roll like a mechanical counter when the score changes (our own take on the
// odometer idea: every place is a strip of 0-9 moved with a CSS transform, so only the changed place moves).
// roll-in: start from 0 and roll to the value once mounted (a finished match's final score).
// The strips are hidden from screen readers; the number itself is read once.
import { computed, onMounted, ref, watch } from 'vue'
import { reducedMotion } from './core'

const props = defineProps({
  value: { type: Number, required: true },
  minDigits: { type: Number, default: 1 },
  rollIn: Boolean,
})

const shown = ref(props.rollIn && !reducedMotion.value ? 0 : props.value)
watch(() => props.value, (v) => { shown.value = v })
onMounted(() => {
  if (shown.value !== props.value) requestAnimationFrame(() => requestAnimationFrame(() => { shown.value = props.value }))
})

// keyed by place (ones, tens, ...), so the ones strip stays the same element when 9 becomes 10
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
