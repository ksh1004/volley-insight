<script setup>
// 홈 화면의 '진행 중 경기' 카드 하나: 세트 스코어, 지금 세트 점수, 서브권(●). 누르면 실시간 화면으로 간다.
// 모션: 홈 화면이 20초마다 새로 받아 오면 점수 숫자가 굴러간다(RollingNumber).
import { computed } from 'vue'
import { GENDERS, LIVE_MODES } from '@/constants/volley'
import RollingNumber from '@/motion/RollingNumber.vue'

const props = defineProps({ item: { type: Object, required: true } }) // { match, state } from /api/live
const m = computed(() => props.item.match)
const s = computed(() => props.item.state)
const finished = computed(() => s.value.status === 'FINISHED')
// 이 팀이 지금 서브권을 가졌는가(끝난 경기는 없음)
const serving = (code) => !finished.value && s.value.serving === code
const status = computed(() => (finished.value ? '종료' : s.value.set_open ? `${s.value.current_set}세트` : '세트 사이'))
// 화면 낭독기용 카드 설명 한 문장
const label = computed(() => {
  const sets = `세트 스코어 ${s.value.home_sets} 대 ${s.value.away_sets}`
  const pts = finished.value ? '' : `, ${status.value} ${s.value.home_points} 대 ${s.value.away_points}`
  return `${LIVE_MODES[s.value.mode]} ${finished.value ? '종료' : '진행 중'}: ${m.value.home.name} 대 ${m.value.away.name}, ${sets}${pts}`
})
</script>

<template>
  <RouterLink :to="`/matches/${m.key}/live`" class="live-card lift" :class="{ replay: s.mode === 'REPLAY' }" :aria-label="label">
    <div class="meta small">
      <span class="mode" :class="s.mode === 'REPLAY' ? 'replay' : 'live'">
        <i v-if="s.mode === 'LIVE' && !finished" class="dot" aria-hidden="true" />{{ LIVE_MODES[s.mode] }}
      </span>
      <span class="muted">{{ GENDERS[m.gender] }} · {{ status }}</span>
    </div>
    <div class="row">
      <span class="team home"><i v-if="serving(s.home)" class="serve" title="서브" />{{ m.home.name }}</span>
      <span class="sets" aria-hidden="true"><b><RollingNumber :value="s.home_sets" /></b>:<b><RollingNumber :value="s.away_sets" /></b></span>
      <span class="team away">{{ m.away.name }}<i v-if="serving(s.away)" class="serve" title="서브" /></span>
    </div>
    <div v-if="!finished" class="pts" aria-hidden="true"><span class="h"><RollingNumber :value="s.home_points" /></span> : <span class="a"><RollingNumber :value="s.away_points" /></span></div>
  </RouterLink>
</template>

<style scoped>
.live-card { display: block; padding: 10px 14px; border: 1px solid var(--color-accent); border-radius: var(--radius);
             background: var(--color-card); color: var(--color-foreground);
             transition: box-shadow .15s var(--ease), transform var(--dur-fast) var(--ease-out); }
.live-card.replay { border-color: var(--color-border); }
.live-card:hover { text-decoration: none; box-shadow: 0 2px 8px rgb(30 64 175 / .1); }
.meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.mode { display: inline-flex; align-items: center; gap: 5px; font-weight: 700; border-radius: 999px; padding: 0 8px; }
.mode.live { color: var(--color-accent); background: var(--color-accent-soft); }
.mode.replay { color: var(--color-muted-foreground); background: var(--color-muted); }
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
.row { display: grid; grid-template-columns: 1fr auto 1fr; gap: 10px; align-items: center; font-weight: 600; }
.team { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: 5px; }
.team.home { justify-content: flex-end; color: var(--color-home); }
.team.away { color: var(--color-away); }
.sets { font-size: 1.2rem; font-weight: 800; font-variant-numeric: tabular-nums; display: flex; gap: 4px; }
.pts { text-align: center; font-size: .9rem; font-variant-numeric: tabular-nums; color: var(--color-muted-foreground); }
.pts .h { color: var(--color-home); font-weight: 700; }
.pts .a { color: var(--color-away); font-weight: 700; }
.serve { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--color-foreground); flex: none; }
</style>
