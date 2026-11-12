<script setup>
// 승리 확률을 100% 누적 막대로, 양쪽 퍼센트를 글자로 함께 적어 보여 준다
// (ui-ux-pro-max 차트 지침: 비슷한 값의 부분-전체 비교 → 누적 막대 + 직접 표기, 색만으로 전하지 않음).
// 모션: 처음 보일 때 가운데에서 양쪽으로 열리고, 실시간 경기 중에는 나뉜 지점이 미끄러지며 퍼센트도 새 값으로
// 따라간다(transform만 사용, 레이아웃 변화 없음).
import { computed, ref } from 'vue'
import AnimatedNumber from '@/motion/AnimatedNumber.vue'
import { useInView } from '@/motion/core'

const props = defineProps({
  home: { type: String, required: true },
  away: { type: String, required: true },
  prob: { type: Number, required: true }, // 홈 승리 확률 0~1
  compact: Boolean, // 경기 카드 안에 넣는 작은 버전
  label: { type: String, default: '예측 승리 확률' }, // 화면 낭독기가 읽는 이름(예: 실시간 승리 확률)
})
const h = computed(() => Math.round(props.prob * 100))
const a = computed(() => 100 - h.value)
// 우세한 쪽('home' | 'away', 같으면 null): 그쪽 숫자를 강조
const favourite = computed(() => (h.value === a.value ? null : h.value > a.value ? 'home' : 'away'))
const pctText = (v) => `${Math.round(v)}%`

const el = ref(null)
const seen = useInView(el)
// 보이기 전에는 양쪽이 가운데 선에 붙어 있다가, 보이면 실제 비율로 열린다
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
.labels { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-family: var(--font-outlier);
          font-variant-numeric: tabular-nums; }
.labels .home { color: var(--color-home); }
.labels .away { color: var(--color-away); }
.labels .fav { font-weight: 700; }
.nm { margin-right: .3em; }
.nm.after { margin: 0 0 0 .3em; }
.cap { font-family: var(--font-body); font-size: .75rem; color: var(--color-muted-foreground); }
.bar { position: relative; height: 10px; border-radius: 2px; overflow: hidden; margin-top: 4px; background: var(--color-muted); }
.bar i { position: absolute; inset: 0; display: block; will-change: transform; }
.bar .home { background: var(--color-home); transform-origin: left center; }
.bar .away { background: var(--color-away); transform-origin: right center; }
/* the 2px gap between the two sides travels with the split */
.bar .cut { background: none; }
.bar .cut::before { content: ''; position: absolute; left: -1px; top: 0; bottom: 0; width: 2px; background: var(--color-paper-2); }
.seen .bar i { transition: transform var(--dur-chart) var(--ease-out); }
.wp:not(.compact) .labels { font-size: 1.05rem; }
.compact .labels { font-size: .78rem; }
.compact .bar { height: 6px; margin-top: 3px; }
</style>
