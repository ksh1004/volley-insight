<script setup>
// One match as a compact card: time, teams, sets (winner in bold + colour), set scores. Links to the match page.
import { computed } from 'vue'
import WinProbBar from '@/components/WinProbBar.vue'
import { GENDERS, STATUS } from '@/constants/volley'
import { fmtDate, fmtTime } from '@/utils/format'

// `favorite`: one of the viewer's favourite teams plays (WEB-12)
const props = defineProps({ match: { type: Object, required: true }, showDate: Boolean, favorite: Boolean })
const m = computed(() => props.match)
const done = computed(() => ['FINISHED', 'CONFIRMED'].includes(m.value.status))
const live = computed(() => m.value.status === 'LIVE')
const homeWon = computed(() => done.value && m.value.home_sets > m.value.away_sets)
const awayWon = computed(() => done.value && m.value.away_sets > m.value.home_sets)
const when = computed(() => (props.showDate ? `${fmtDate(m.value.scheduled_at)} ${fmtTime(m.value.scheduled_at)}` : fmtTime(m.value.scheduled_at)))
const stage = computed(() => (m.value.league_code === '201' ? `${m.value.round}라운드` : m.value.competition))
const label = computed(() => {
  const score = done.value ? `${m.value.home_sets} 대 ${m.value.away_sets}, ${homeWon.value ? m.value.home.name : m.value.away.name} 승`
    : live.value ? `진행 중, 세트 스코어 ${m.value.home_sets} 대 ${m.value.away_sets}, 실시간 화면으로 이동` : STATUS[m.value.status]
  const p = m.value.prediction
  const pred = !done.value && p ? `, 예측 승리 확률 ${m.value.home.name} ${Math.round(p.home_win_prob * 100)}퍼센트` : ''
  return `${props.favorite ? '관심 팀 경기, ' : ''}${when.value} ${GENDERS[m.value.gender]} ${m.value.home.name} 대 ${m.value.away.name}, ${score}${pred}`
})
</script>

<template>
  <RouterLink :to="live ? `/matches/${m.key}/live` : `/matches/${m.key}`" class="match-card lift" :class="{ post: m.league_code !== '201' }" :aria-label="label">
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
        <span class="pill"><i class="dot" aria-hidden="true" />진행 중</span>
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
.match-card { display: block; padding: 10px 14px 12px; border: 1px solid var(--color-border); border-radius: var(--radius);
              background: var(--color-card); color: var(--color-foreground);
              transition: border-color .15s var(--ease), box-shadow .15s var(--ease), transform var(--dur-fast) var(--ease-out); }
.match-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgb(30 64 175 / .08); text-decoration: none; }
.match-card.post { border-left: 3px solid var(--color-accent); }
.meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: .78rem; color: var(--color-muted-foreground); margin-bottom: 6px; }
.when { display: inline-flex; align-items: center; gap: 4px; color: var(--color-foreground); font-weight: 500; font-variant-numeric: tabular-nums; }
.star { width: 14px; height: 14px; fill: var(--color-accent); }
.tags { display: flex; gap: 6px; }
.tags .stage { color: var(--color-accent); font-weight: 700; }
.teams { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px; font-weight: 500; }
.team { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.team.home { text-align: right; }
.team.won { color: var(--color-heading); font-weight: 700; }
.team.lost { color: var(--color-muted-foreground); }
.score { display: flex; align-items: baseline; gap: 6px; font-size: 1.25rem; min-width: 64px; justify-content: center; font-variant-numeric: tabular-nums; }
.score b { font-weight: 500; color: var(--color-muted-foreground); }
.score b.won { color: var(--color-primary); font-weight: 700; }
.score i { font-style: normal; color: var(--color-muted-foreground); font-size: 1rem; }
.score.pending { font-size: .78rem; color: var(--color-muted-foreground); background: var(--color-muted); border-radius: 999px; padding: 2px 10px; }
.foot { text-align: center; margin-top: 6px; color: var(--color-muted-foreground); display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;
        font-variant-numeric: tabular-nums; }
.set .w { color: var(--color-foreground); font-weight: 700; }
.prob { margin-top: 8px; }
.score.live { flex-direction: column; align-items: center; gap: 2px; }
.score.live .sets { display: flex; gap: 6px; align-items: baseline; }
.score.live .sets b { color: var(--color-foreground); font-weight: 700; }
.pill { display: inline-flex; align-items: center; gap: 5px; font-size: .72rem; font-weight: 700; color: var(--color-accent);
        background: var(--color-accent-soft); border-radius: 999px; padding: 1px 8px; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
.hit { text-align: center; margin-top: 6px; }
</style>
