<script setup>
// 경기 하나를 작은 카드로: 시각, 두 팀, 세트 스코어(이긴 쪽은 굵게 + 색), 세트별 점수, (예정 경기면) 예측 승리 확률.
// 누르면 경기 화면으로, 진행 중 경기면 실시간 화면으로 간다.
import { computed } from 'vue'
import WinProbBar from '@/components/WinProbBar.vue'
import { GENDERS, STATUS } from '@/constants/volley'
import { fmtDate, fmtTime } from '@/utils/format'

// `favorite`: 보는 사람의 관심 팀이 뛰는 경기(테두리 강조, WEB-12). showDate: 시각 앞에 날짜도
const props = defineProps({ match: { type: Object, required: true }, showDate: Boolean, favorite: Boolean })
const m = computed(() => props.match)
const done = computed(() => ['FINISHED', 'CONFIRMED'].includes(m.value.status))
const live = computed(() => m.value.status === 'LIVE')
const homeWon = computed(() => done.value && m.value.home_sets > m.value.away_sets)
const awayWon = computed(() => done.value && m.value.away_sets > m.value.home_sets)
const when = computed(() => (props.showDate ? `${fmtDate(m.value.scheduled_at)} ${fmtTime(m.value.scheduled_at)}` : fmtTime(m.value.scheduled_at)))
// 정규리그면 "3라운드", 포스트시즌이면 대회 이름
const stage = computed(() => (m.value.league_code === '201' ? `${m.value.round}라운드` : m.value.competition))
// 화면 낭독기용 카드 설명 한 문장(시각, 두 팀, 결과 또는 상태, 예측)
const label = computed(() => {
  const score = done.value ? `${m.value.home_sets} 대 ${m.value.away_sets}, ${homeWon.value ? m.value.home.name : m.value.away.name} 승`
    : live.value ? `진행 중, 세트 스코어 ${m.value.home_sets} 대 ${m.value.away_sets}, 실시간 화면으로 이동` : STATUS[m.value.status]
  const p = m.value.prediction
  const pred = !done.value && p ? `, 예측 승리 확률 ${m.value.home.name} ${Math.round(p.home_win_prob * 100)}퍼센트` : ''
  return `${props.favorite ? '관심 팀 경기, ' : ''}${when.value} ${GENDERS[m.value.gender]} ${m.value.home.name} 대 ${m.value.away.name}, ${score}${pred}`
})
</script>

<template>
  <RouterLink :to="live ? `/matches/${m.key}/live` : `/matches/${m.key}`" class="match-card" :class="{ post: m.league_code !== '201' }" :aria-label="label">
    <div class="meta">
      <span class="when"><svg v-if="favorite" class="star" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" /></svg>{{ when }}</span>
      <span class="tags"><span>{{ GENDERS[m.gender] }}</span><span :class="{ stage: m.league_code !== '201' }">{{ stage }}</span></span>
    </div>
    <div class="teams">
      <span class="team home" :class="{ won: homeWon, lost: awayWon }">{{ m.home.name }}</span>
      <span v-if="done" class="score">
        <b :class="{ won: homeWon }">{{ m.home_sets }}</b><i>:</i><b :class="{ won: awayWon }">{{ m.away_sets }}</b>
      </span>
      <span v-else-if="live" class="score live">
        <span class="pill">진행 중</span>
        <span class="sets"><b>{{ m.home_sets }}</b><i>:</i><b>{{ m.away_sets }}</b></span>
      </span>
      <span v-else class="score pending">{{ STATUS[m.status] }}</span>
      <span class="team away" :class="{ won: awayWon, lost: homeWon }">{{ m.away.name }}</span>
    </div>
    <div class="foot small">
      <template v-if="done && m.sets.length">
        <span v-for="([h, a], i) in m.sets" :key="i" class="set"><span :class="{ w: h > a }">{{ h }}</span>-<span :class="{ w: a > h }">{{ a }}</span></span>
      </template>
      <span v-else class="muted">{{ m.venue }}</span>
    </div>
    <WinProbBar v-if="!done && m.prediction" class="prob" compact :home="m.home.name" :away="m.away.name"
                :prob="m.prediction.home_win_prob" />
    <div v-else-if="done && m.prediction && m.prediction.is_correct != null" class="hit small">
      <span class="badge" :class="m.prediction.is_correct ? 'w' : 'l'">{{ m.prediction.is_correct ? '예측 적중' : '예측 빗나감' }}</span>
    </div>
  </RouterLink>
</template>

<style scoped>
/* 경기 타일: 상자 대신 굵은 줄 아래의 옅은 칸. 팀 이름과 세트 스코어는 표제 글꼴로 크게, 이긴 쪽 숫자는 라임 칸 위에 놓인다 */
.match-card { display: block; padding: var(--space-sm) var(--space-md) var(--space-md); background: var(--color-paper-2);
              border-top: var(--rule-bar) solid var(--color-rule-2); border-radius: 0 0 var(--radius-card) var(--radius-card);
              color: var(--color-ink); text-decoration: none; transition: background-color var(--dur-micro) var(--ease-out); }
.match-card:hover { background: var(--color-paper-3); }
.match-card:active { border-top-color: var(--color-ink); }
.meta { display: flex; justify-content: space-between; align-items: center; gap: var(--space-xs); font-size: var(--text-xs);
        color: var(--color-ink-muted); margin-bottom: var(--space-xs); }
.when { display: inline-flex; align-items: center; gap: var(--space-2xs); color: var(--color-ink); font-family: var(--font-outlier); font-weight: 500;
        font-variant-numeric: tabular-nums; }
.star { width: 14px; height: 14px; fill: var(--color-ink); }
.tags { display: flex; gap: var(--space-xs); }
.tags .stage { color: var(--color-ink); font-weight: 700; }
.teams { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: var(--space-sm);
         font-family: var(--font-display); font-weight: var(--display-weight); font-synthesis: none; font-size: var(--text-lg); line-height: 1.15; }
.team { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.team.home { text-align: right; }
.team.lost { color: var(--color-ink-muted); }
.score { display: flex; align-items: center; gap: var(--space-2xs); justify-content: center; font-size: var(--text-2xl); line-height: 1;
         font-variant-numeric: tabular-nums; }
.score b { min-width: 1.15em; padding: var(--space-3xs) 0; text-align: center; border-radius: 2px; color: var(--color-ink-muted); }
.score b.won { background: var(--color-volt); color: var(--color-volt-ink); }
.score i { font-style: normal; color: var(--color-ink-muted); font-size: var(--text-md); }
.score.pending { font-family: var(--font-body); font-weight: 500; font-size: var(--text-xs); color: var(--color-ink-muted);
                 background: var(--color-paper-3); border-radius: 2px; padding: var(--space-3xs) var(--space-xs); }
.foot { display: flex; gap: var(--space-sm); justify-content: center; flex-wrap: wrap; margin-top: var(--space-xs); text-align: center;
        font-family: var(--font-outlier); font-size: var(--text-xs); color: var(--color-ink-muted); font-variant-numeric: tabular-nums; }
.foot .muted { font-family: var(--font-body); font-size: var(--text-sm); }
.set .w { color: var(--color-ink); font-weight: 700; }
.prob { margin-top: var(--space-sm); }
.score.live { flex-direction: column; gap: var(--space-2xs); }
.score.live .sets { display: flex; gap: var(--space-2xs); align-items: center; }
.score.live .sets b { color: var(--color-ink); }
.pill { font-family: var(--font-body); font-size: var(--text-xs); font-weight: 700; color: var(--color-volt-ink); background: var(--color-volt);
        border-radius: 2px; padding: 0 var(--space-xs); line-height: 1.7; }
.hit { text-align: center; margin-top: var(--space-xs); }
</style>
