<script setup>
// Pre-match win probability as a 100% stacked bar with both percentages written out
// (ui-ux-pro-max chart guidance: part-to-whole with close values -> stacked bar + direct labels, not colour alone).
import { computed } from 'vue'

const props = defineProps({
  home: { type: String, required: true },
  away: { type: String, required: true },
  prob: { type: Number, required: true }, // home win probability 0..1
  compact: Boolean, // small version inside match cards
  label: { type: String, default: '예측 승리 확률' }, // spoken name (e.g. live win probability)
})
const h = computed(() => Math.round(props.prob * 100))
const a = computed(() => 100 - h.value)
const homeText = computed(() => (props.compact ? `${h.value}%` : `${props.home} ${h.value}%`))
const awayText = computed(() => (props.compact ? `${a.value}%` : `${a.value}% ${props.away}`))
const favourite = computed(() => (h.value === a.value ? null : h.value > a.value ? 'home' : 'away'))
</script>

<template>
  <div class="wp" :class="{ compact }" role="img"
       :aria-label="`${label}: ${home} ${h}퍼센트, ${away} ${a}퍼센트`">
    <div class="labels" aria-hidden="true">
      <span class="home" :class="{ fav: favourite === 'home' }">{{ homeText }}</span>
      <span v-if="compact" class="cap">{{ label === '예측 승리 확률' ? '승리 확률' : label }}</span>
      <span class="away" :class="{ fav: favourite === 'away' }">{{ awayText }}</span>
    </div>
    <div class="bar" aria-hidden="true">
      <i class="home" :style="{ width: `${h}%` }" />
      <i class="away" :style="{ width: `${a}%` }" />
    </div>
  </div>
</template>

<style scoped>
.labels { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-variant-numeric: tabular-nums; }
.labels .home { color: var(--color-home); }
.labels .away { color: var(--color-away); }
.labels .fav { font-weight: 700; }
.cap { font-size: .72rem; color: var(--color-muted-foreground); }
.bar { display: flex; height: 10px; border-radius: 999px; overflow: hidden; margin-top: 4px; background: var(--color-muted); gap: 2px; }
.bar i { display: block; height: 100%; }
.bar .home { background: var(--color-home); }
.bar .away { background: var(--color-away); }
.wp:not(.compact) .labels { font-size: 1.05rem; }
.compact .labels { font-size: .78rem; }
.compact .bar { height: 6px; margin-top: 3px; }
</style>
