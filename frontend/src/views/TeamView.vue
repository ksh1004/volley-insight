<script setup>
// WEB-06: season record, indicators vs league average, roster, recent and upcoming matches.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getTeam } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import LeagueCompare from '@/components/LeagueCompare.vue'
import MatchCard from '@/components/MatchCard.vue'
import RatingChart from '@/components/RatingChart.vue'
import SortableTable from '@/components/SortableTable.vue'
import { GENDERS, POSITIONS } from '@/constants/volley'
import { num, pct, ratio } from '@/utils/format'

const props = defineProps({ code: { type: String, required: true } })
const data = ref(null)
const route = useRoute()
const season = ref(route.query.season || '')
const state = ref({ loading: true, error: null })

async function load() {
  state.value = { loading: true, error: null }
  try {
    data.value = await getTeam(props.code, season.value)
    season.value = data.value.season.code
  } catch (e) {
    state.value.error = e.response?.status === 404 ? '팀을 찾을 수 없습니다.' : e.message
  } finally {
    state.value.loading = false
  }
}
onMounted(load)
watch(season, (v) => { if (data.value && v !== data.value.season.code) load() })

const two = (v) => num(v, 2)
const ROSTER_COLS = [
  { key: 'name', label: '선수', left: true, sticky: true, get: (p) => p.player.name },
  { key: 'back_number', label: '번호' },
  { key: 'position', label: '포지션', left: true, get: (p) => POSITIONS[p.position] || null },
  { key: 'games', label: '경기' },
  { key: 'sets_played', label: '세트' },
  { key: 'points', label: '득점', strong: true },
  { key: 'attack_pct', label: '공격 성공률', fmt: (p) => pct(p.attack_pct) },
  { key: 'blocks_per_set', label: '세트당 블로킹', fmt: (p) => two(p.blocks_per_set) },
  { key: 'serve_aces_per_set', label: '세트당 서브', fmt: (p) => two(p.serve_aces_per_set) },
  { key: 'receive_efficiency', label: '리시브 효율', fmt: (p) => pct(p.receive_efficiency) },
  { key: 'digs_per_set', label: '세트당 디그', fmt: (p) => two(p.digs_per_set) },
]
// rank of this team's current Elo among the league's teams
const ratingRank = computed(() => {
  const all = Object.entries(data.value?.rating.league || {}).sort((a, b) => b[1] - a[1])
  const i = all.findIndex(([code]) => code === props.code)
  return i < 0 ? null : { rank: i + 1, teams: all.length }
})
const rows = computed(() => {
  const t = data.value?.stats.team
  const l = data.value?.stats.league
  if (!t || !l) return []
  return [
    { label: '공격 성공률', a: t.attack_pct, b: l.attack_pct, fmt: pct },
    { label: '공격 효율', a: t.attack_efficiency, b: l.attack_efficiency, fmt: pct },
    { label: '세트당 블로킹', a: t.blocks_per_set, b: l.blocks_per_set, fmt: two },
    { label: '세트당 서브', a: t.serve_aces_per_set, b: l.serve_aces_per_set, fmt: two },
    { label: '리시브 효율', a: t.receive_efficiency, b: l.receive_efficiency, fmt: pct },
    { label: '세트당 디그', a: t.digs_per_set, b: l.digs_per_set, fmt: two },
    { label: '세트당 범실', a: t.errors_per_set, b: l.errors_per_set, fmt: two, lowerIsBetter: true },
    // side-out / break come from play-by-play, available from 2023-24
    ...(t.sideout_rate != null ? [
      { label: '사이드아웃율', a: t.sideout_rate, b: l.sideout_rate, fmt: pct },
      { label: '브레이크율', a: t.break_rate, b: l.break_rate, fmt: pct },
    ] : []),
  ]
})
</script>

<template>
  <AsyncState :loading="state.loading && !data" :error="state.error">
    <template v-if="data">
      <div class="title-row">
        <div>
          <h1>{{ data.team.name }}</h1>
          <p class="muted small">{{ data.team.full_name }} · {{ GENDERS[data.team.gender] }}
            <span v-if="data.team.home_arena"> · 홈 {{ data.team.home_arena }}</span></p>
        </div>
        <select v-model="season" aria-label="시즌">
          <option v-for="s in data.seasons" :key="s.code" :value="s.code">{{ s.years.replace('-', '–') }} 시즌</option>
        </select>
      </div>

      <section class="card record" v-if="data.record">
        <div><span class="muted small">순위</span><b>{{ data.record.rank }}위</b></div>
        <div><span class="muted small">승패</span><b>{{ data.record.wins }}승 {{ data.record.losses }}패</b></div>
        <div><span class="muted small">승점</span><b>{{ data.record.points }}</b></div>
        <div><span class="muted small">세트 득실률</span><b>{{ data.record.games ? ratio(data.record.set_ratio) : '-' }}</b></div>
        <div><span class="muted small">최근</span>
          <b class="form"><span v-for="(f, i) in data.record.form" :key="i" class="badge" :class="f.toLowerCase()">{{ f === 'W' ? '승' : '패' }}</span></b>
        </div>
      </section>

      <section class="card">
        <h2>전력 지수 추이 <span class="muted small">Elo · 정규리그와 포스트시즌</span></h2>
        <RatingChart :history="data.rating.history" :team-name="data.team.name"
                     :rank="ratingRank?.rank" :teams="ratingRank?.teams" />
      </section>

      <div class="grid-2">
        <section class="card">
          <h2>리그 평균과 비교 <span class="muted small">정규리그</span></h2>
          <LeagueCompare v-if="rows.length" :rows="rows" />
          <p v-else class="empty">아직 끝난 경기가 없습니다.</p>
        </section>
        <section class="card">
          <h2>최근 경기</h2>
          <div class="stack">
            <MatchCard v-for="m in data.recent" :key="m.key" :match="m" show-date />
            <p v-if="!data.recent.length" class="empty">아직 끝난 경기가 없습니다.</p>
          </div>
          <template v-if="data.upcoming.length">
            <h2 class="mt">다음 경기</h2>
            <div class="stack"><MatchCard v-for="m in data.upcoming" :key="m.key" :match="m" show-date /></div>
          </template>
        </section>
      </div>

      <section class="card">
        <h2>선수단 기록 <span class="muted small">정규리그, 출전 선수</span></h2>
        <SortableTable v-if="data.roster.length" :columns="ROSTER_COLS" :rows="data.roster" :row-key="(p) => p.player.code"
                       :initial-sort="{ key: 'points', dir: 'desc' }" caption="선수단 시즌 기록">
          <template #cell-name="{ row }">
            <RouterLink :to="`/players/${row.player.code}?season=${data.season.code}`">{{ row.player.name }}</RouterLink>
          </template>
        </SortableTable>
        <p v-if="!data.roster.length" class="empty">이 시즌 출전 기록이 없습니다.</p>
      </section>
    </template>
  </AsyncState>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.title-row h1 { margin-bottom: 4px; }
.title-row p { margin: 0 0 16px; }
.record { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
.record div { display: flex; flex-direction: column; }
.record b { font-size: 1.25rem; }
.form { display: flex; gap: 3px; }
.stack { display: grid; gap: 8px; }
.mt { margin-top: 16px; }
</style>
