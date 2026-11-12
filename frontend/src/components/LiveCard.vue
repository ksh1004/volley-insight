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
  <RouterLink :to="`/matches/${m.key}/live`" class="live-card" :class="{ replay: s.mode === 'REPLAY' }" :aria-label="label">
    <div class="meta small">
      <span class="mode" :class="s.mode === 'REPLAY' ? 'replay' : 'live'">
        {{ LIVE_MODES[s.mode] }}
      </span>
      <span class="where"><span>{{ GENDERS[m.gender] }}</span><span>{{ status }}</span></span>
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
/* 진행 중 경기 타일: 전광판처럼 잉크색 바탕에 밝은 글자. 실시간이면 위에 라임색 굵은 줄, 재생이면 회색 줄 */
.live-card { --on-ink-soft: color-mix(in oklab, var(--color-paper) 72%, transparent);
             display: block; padding: var(--space-sm) var(--space-md) var(--space-md); background: var(--color-ink); color: var(--color-paper);
             border-top: 4px solid var(--color-volt); border-radius: 0 0 var(--radius-card) var(--radius-card); text-decoration: none;
             transition: background-color var(--dur-micro) var(--ease-out); }
.live-card.replay { border-top-color: var(--color-rule); }
.live-card:hover, .live-card:active { background: var(--color-ink-2); }
.live-card:focus-visible { outline-color: var(--color-volt-deep); }
.meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-xs); font-size: var(--text-xs); }
.mode { font-weight: 700; border-radius: 2px; padding: 0 var(--space-xs); line-height: 1.7; }
.mode.live { color: var(--color-volt-ink); background: var(--color-volt); }
.mode.replay { color: var(--color-paper); background: color-mix(in oklab, var(--color-paper) 18%, transparent); }
.where { display: flex; gap: var(--space-xs); color: var(--on-ink-soft); }
.row { display: grid; grid-template-columns: 1fr auto 1fr; gap: var(--space-sm); align-items: center;
       font-family: var(--font-display); font-weight: var(--display-weight); font-synthesis: none; font-size: var(--text-lg); line-height: 1.15; }
.team { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: var(--space-2xs); }
.team.home { justify-content: flex-end; }
.sets { font-size: var(--text-2xl); line-height: 1; font-variant-numeric: tabular-nums; display: flex; gap: var(--space-2xs); }
.pts { margin-top: var(--space-2xs); text-align: center; font-family: var(--font-outlier); font-size: var(--text-sm);
       font-variant-numeric: tabular-nums; color: var(--on-ink-soft); }
.pts .h, .pts .a { color: var(--color-paper); font-weight: 700; }
.serve { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--color-paper); flex: none; } /* 서브권 표시 */
</style>
