<script setup>
// One match as a compact card: time, teams, sets (winner in bold + colour), set scores. Links to the match page.
import { computed } from 'vue'
import { GENDERS, STATUS } from '@/constants/volley'
import { fmtDate, fmtTime } from '@/utils/format'

const props = defineProps({ match: { type: Object, required: true }, showDate: Boolean })
const m = computed(() => props.match)
const done = computed(() => ['FINISHED', 'CONFIRMED'].includes(m.value.status))
const homeWon = computed(() => done.value && m.value.home_sets > m.value.away_sets)
const awayWon = computed(() => done.value && m.value.away_sets > m.value.home_sets)
const when = computed(() => (props.showDate ? `${fmtDate(m.value.scheduled_at)} ${fmtTime(m.value.scheduled_at)}` : fmtTime(m.value.scheduled_at)))
const stage = computed(() => (m.value.league_code === '201' ? `${m.value.round}라운드` : m.value.competition))
const label = computed(() => {
  const score = done.value ? `${m.value.home_sets} 대 ${m.value.away_sets}, ${homeWon.value ? m.value.home.name : m.value.away.name} 승` : STATUS[m.value.status]
  return `${when.value} ${GENDERS[m.value.gender]} ${m.value.home.name} 대 ${m.value.away.name}, ${score}`
})
</script>

<template>
  <RouterLink :to="`/matches/${m.key}`" class="match-card" :class="{ post: m.league_code !== '201' }" :aria-label="label">
    <div class="meta">
      <span class="when">{{ when }}</span>
      <span class="tags"><span>{{ GENDERS[m.gender] }}</span><span :class="{ stage: m.league_code !== '201' }">{{ stage }}</span></span>
    </div>
    <div class="teams">
      <span class="team home" :class="{ won: homeWon, lost: awayWon }">{{ m.home.name }}</span>
      <span v-if="done" class="score">
        <b :class="{ won: homeWon }">{{ m.home_sets }}</b><i>:</i><b :class="{ won: awayWon }">{{ m.away_sets }}</b>
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
  </RouterLink>
</template>

<style scoped>
.match-card { display: block; padding: 10px 14px 12px; border: 1px solid var(--color-border); border-radius: var(--radius);
              background: var(--color-card); color: var(--color-foreground);
              transition: border-color .15s var(--ease), box-shadow .15s var(--ease); }
.match-card:hover { border-color: var(--color-primary); box-shadow: 0 2px 8px rgb(30 64 175 / .08); text-decoration: none; }
.match-card.post { border-left: 3px solid var(--color-accent); }
.meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: .78rem; color: var(--color-muted-foreground); margin-bottom: 6px; }
.when { color: var(--color-foreground); font-weight: 500; font-variant-numeric: tabular-nums; }
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
</style>
