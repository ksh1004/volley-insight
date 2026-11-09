<script setup>
// Player indicators with the percentile among qualified players of the same position (ANL-05).
// Shows the indicators that matter for the position; the percentile is written as text, the bar only repeats it.
// Motion: the bars fill from the left one after another when first seen, and the percentile counts up with them.
import { computed, ref } from 'vue'
import { POSITIONS } from '@/constants/volley'
import AnimatedNumber from '@/motion/AnimatedNumber.vue'
import { useInView } from '@/motion/core'
import { num } from '@/utils/format'

const props = defineProps({ profile: { type: Object, required: true } })
const el = ref(null)
const seen = useInView(el)

const LABELS = {
  attack_success_rate: ['공격 성공률', (v) => `${num(v, 1)}%`],
  attack_efficiency: ['공격 효율', (v) => `${num(v, 1)}%`],
  receive_efficiency: ['리시브 효율', (v) => `${num(v, 1)}%`],
  block_per_set: ['세트당 블로킹', (v) => num(v, 2)],
  serve_per_set: ['세트당 서브 에이스', (v) => num(v, 2)],
  dig_per_set: ['세트당 디그', (v) => num(v, 2)],
  set_per_set: ['세트당 세트', (v) => num(v, 2)],
  error_per_set: ['세트당 범실 (적을수록 좋음)', (v) => num(v, 2)],
}
const BY_POSITION = {
  OH: ['attack_success_rate', 'attack_efficiency', 'receive_efficiency', 'serve_per_set', 'dig_per_set', 'block_per_set'],
  OP: ['attack_success_rate', 'attack_efficiency', 'serve_per_set', 'block_per_set', 'error_per_set'],
  MB: ['attack_success_rate', 'attack_efficiency', 'block_per_set', 'serve_per_set'],
  S: ['set_per_set', 'serve_per_set', 'block_per_set', 'dig_per_set'],
  L: ['receive_efficiency', 'dig_per_set'],
}
const rows = computed(() => (BY_POSITION[props.profile.position] || Object.keys(LABELS))
  .filter((k) => props.profile.values[k] != null)
  .map((k) => ({ key: k, label: LABELS[k][0], value: LABELS[k][1](props.profile.values[k]), pct: props.profile.percentiles[k] })))
const tier = (p) => (p == null ? '' : p >= 80 ? 'top' : p >= 50 ? 'mid' : 'low')
</script>

<template>
  <div ref="el">
    <p class="small muted lead">
      같은 시즌 {{ POSITIONS[profile.position] || '같은 포지션' }} 중 순위 자격이 있는 선수와 비교한 백분위예요.
      100에 가까울수록 상위예요.
    </p>
    <p v-if="!profile.qualified" class="small notice">
      이 시즌 출전 세트({{ profile.sets }})가 순위 자격(팀 세트의 1/3)에 못 미쳐 백분위는 계산하지 않았어요.
    </p>
    <ul class="bars">
      <li v-for="(r, i) in rows" :key="r.key">
        <span class="label small">{{ r.label }}</span>
        <span class="value">{{ r.value }}</span>
        <span class="track" aria-hidden="true"><i :class="tier(r.pct)"
          :style="{ transform: `scaleX(${seen ? (r.pct ?? 0) / 100 : 0})`, transitionDelay: `${i * 60}ms` }" /></span>
        <span class="pct small" :class="tier(r.pct)"><AnimatedNumber :value="r.pct" count-up /></span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.lead { margin: 0 0 var(--space-2); }
.notice { background: var(--color-accent-soft); color: var(--color-accent); border-radius: 8px; padding: 6px 10px; }
.bars { list-style: none; padding: 0; margin: 0; }
.bars li { display: grid; grid-template-columns: 190px 64px 1fr 36px; gap: 10px; align-items: center; padding: 5px 0;
           border-bottom: 1px solid var(--color-border-soft); }
.label { color: var(--color-muted-foreground); }
.value { font-weight: 700; font-variant-numeric: tabular-nums; text-align: right; }
.track { height: 8px; background: var(--color-muted); border-radius: 4px; overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 4px; background: var(--color-muted-foreground);
           transform-origin: left center; transition: transform var(--dur-chart) var(--ease-out); }
.track i.top { background: var(--color-primary); }
.track i.mid { background: color-mix(in srgb, var(--color-primary) 55%, white); }
.pct { text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; }
.pct.top { color: var(--color-primary); }
@media (max-width: 560px) { .bars li { grid-template-columns: 110px 56px 1fr 30px; } }
</style>
