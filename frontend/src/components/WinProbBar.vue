<script setup>
// Win probability as a 100% stacked bar with both percentages written out
// (ui-ux-pro-max chart guidance: part-to-whole with close values -> stacked bar + direct labels, not colour alone).
// Motion: the bar grows in from the middle the first time it is seen; during a live game the split slides and
// the percentages glide to each new value (transform only, no layout change).
import { computed, ref } from 'vue'
import AnimatedNumber from '@/motion/AnimatedNumber.vue'
import { useInView } from '@/motion/core'

const props = defineProps({
  home: { type: String, required: true },
  away: { type: String, required: true },
  prob: { type: Number, required: true }, // home win probability 0..1
  compact: Boolean, // small version inside match cards
  label: { type: String, default: '예측 승리 확률' }, // spoken name (e.g. live win probability)
})
const h = computed(() => Math.round(props.prob * 100))
const a = computed(() => 100 - h.value)
const favourite = computed(() => (h.value === a.value ? null : h.value > a.value ? 'home' : 'away'))
const pctText = (v) => `${Math.round(v)}%`

const el = ref(null)
const seen = useInView(el)
// before it is seen both sides sit at the middle line, then they open to the real split
const split = computed(() => (seen.value ? props.prob : 0.5))
</script>

<template>
  <div ref="el" class="wp" :class="{ compact, seen }" role="img"
       :aria-label="`${label}: ${home} ${h}퍼센트, ${away} ${a}퍼센트`">
    <div class="labels" aria-hidden="true">
      <span class="home" :class="{ fav: favourite === 'home' }">
        <span v-if="!compact" class="nm">{{ home }}</span><AnimatedNumber :value="h" :format="pctText" />
      </span>
      <span v-if="compact" class="cap">{{ label === '예측 승리 확률' ? '승리 확률' : label }}</span>
      <span class="away" :class="{ fav: favourite === 'away' }">
        <AnimatedNumber :value="a" :format="pctText" /><span v-if="!compact" class="nm after">{{ away }}</span>
      </span>
    </div>
    <div class="bar" aria-hidden="true">
      <i class="home" :style="{ transform: `scaleX(${split})` }" />
      <i class="away" :style="{ transform: `scaleX(${1 - split})` }" />
      <i class="cut" :style="{ transform: `translateX(${split * 100}%)` }" />
    </div>
  </div>
</template>

<style scoped>
.labels { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-variant-numeric: tabular-nums; }
.labels .home { color: var(--color-home); }
.labels .away { color: var(--color-away); }
.labels .fav { font-weight: 700; }
.nm { margin-right: .3em; }
.nm.after { margin: 0 0 0 .3em; }
.cap { font-size: .72rem; color: var(--color-muted-foreground); }
.bar { position: relative; height: 10px; border-radius: 999px; overflow: hidden; margin-top: 4px; background: var(--color-muted); }
.bar i { position: absolute; inset: 0; display: block; will-change: transform; }
.bar .home { background: var(--color-home); transform-origin: left center; }
.bar .away { background: var(--color-away); transform-origin: right center; }
/* the 2px gap between the two sides travels with the split */
.bar .cut { background: none; }
.bar .cut::before { content: ''; position: absolute; left: -1px; top: 0; bottom: 0; width: 2px; background: var(--color-card); }
.seen .bar i { transition: transform var(--dur-chart) var(--ease-out); }
.wp:not(.compact) .labels { font-size: 1.05rem; }
.compact .labels { font-size: .78rem; }
.compact .bar { height: 6px; margin-top: 3px; }
</style>
