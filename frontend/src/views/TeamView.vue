<script setup>
// WEB-06: season record, indicators vs league average, roster, recent and upcoming matches.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getTeam } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FavoriteButton from '@/components/FavoriteButton.vue'
import LeagueCompare from '@/components/LeagueCompare.vue'
import MatchCard from '@/components/MatchCard.vue'
import RadarChart from '@/components/RadarChart.vue'
import RatingChart from '@/components/RatingChart.vue'
import RelatedArticles from '@/components/RelatedArticles.vue'
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
    // play-by-play situations (ANL-11): first attack after the team's reception, rallies from 20:20 on
    ...(t.attack_after_good_receive != null ? [
      { label: '정확한 리시브 후 공격', a: t.attack_after_good_receive, b: l.attack_after_good_receive, fmt: pct },
      { label: '흔들린 리시브 후 공격', a: t.attack_after_poor_receive, b: l.attack_after_poor_receive, fmt: pct },
    ] : []),
    ...(t.clutch_win_rate != null ? [
      { label: `접전 랠리 승률 (${t.clutch_rallies}회)`, a: t.clutch_win_rate, b: l.clutch_win_rate, fmt: pct },
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
        <div class="actions">
          <FavoriteButton kind="team" :code="data.team.code" :name="data.team.name" />
          <select v-model="season" aria-label="시즌">
            <option v-for="s in data.seasons" :key="s.code" :value="s.code">{{ s.years.replace('-', '–') }} 시즌</option>
          </select>
        </div>
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

      <section v-reveal class="card">
        <h2>전력 지수 추이 <span class="muted small">Elo · 정규리그와 포스트시즌</span></h2>
        <RatingChart :key="data.season.code" :history="data.rating.history" :team-name="data.team.name"
                     :rank="ratingRank?.rank" :teams="ratingRank?.teams" :previous="data.rating.previous"
                     :season-years="data.season.years" />
      </section>

      <div class="grid-2">
        <section v-reveal class="card">
          <h2>강약점 <span class="muted small">정규리그 {{ data.profile.games }}경기 · 리그 {{ data.profile.axes[0]?.teams || '-' }}팀 중 순위</span></h2>
          <RadarChart v-if="data.profile.games && data.profile.axes.some((a) => a.score != null)"
                      :axes="data.profile.axes" :team="data.team.name" />
          <p v-else class="empty">아직 끝난 경기가 없습니다.</p>
          <p v-if="data.profile.games && data.profile.games < 5" class="muted small note">
            시즌 초반이라 경기 수가 적어 순위가 크게 바뀔 수 있습니다.
          </p>
        </section>
        <section v-reveal class="card">
          <h2>리그 평균과 비교 <span class="muted small">정규리그</span></h2>
          <LeagueCompare v-if="rows.length" :rows="rows" />
          <p v-else class="empty">아직 끝난 경기가 없습니다.</p>
          <p v-if="data.stats.team?.clutch_win_rate != null" class="muted small note">
            리시브 후 공격은 리시브 직후 첫 공격의 성공률, 접전 랠리는 양 팀 모두 20점(5세트는 10점) 이상에서 시작한 랠리입니다.
            문자중계가 있는 2023–24 시즌부터 계산합니다.
          </p>
        </section>
      </div>

      <section v-reveal class="card" v-if="data.form.length">
        <h2>선수 폼 변화 <span class="muted small">최근 5경기 vs 그 전 경기 · 뚜렷한 변화만</span></h2>
        <ul class="form-list">
          <li v-for="r in data.form" :key="r.player.code">
            <RouterLink :to="`/players/${r.player.code}?season=${data.season.code}`" class="who">{{ r.player.name }}</RouterLink>
            <span v-for="m in r.metrics" :key="m.key" class="chg small" :class="m.trend">
              {{ m.trend === 'up' ? '▲' : '▼' }} {{ m.label }}
              {{ m.key === 'points_per_set' ? m.before.toFixed(2) : `${m.before.toFixed(1)}%` }} →
              <b>{{ m.key === 'points_per_set' ? m.recent.toFixed(2) : `${m.recent.toFixed(1)}%` }}</b>
            </span>
          </li>
        </ul>
      </section>

      <div class="grid-2">
        <section v-reveal class="card">
          <h2>최근 경기</h2>
          <div class="stack">
            <MatchCard v-for="m in data.recent" :key="m.key" :match="m" show-date />
            <p v-if="!data.recent.length" class="empty">아직 끝난 경기가 없습니다.</p>
          </div>
        </section>
        <section v-reveal class="card">
          <h2>다음 경기</h2>
          <div class="stack">
            <MatchCard v-for="m in data.upcoming" :key="m.key" :match="m" show-date />
            <p v-if="!data.upcoming.length" class="empty">예정된 경기가 없습니다.</p>
          </div>
        </section>
      </div>

      <section v-reveal class="card">
        <h2>선수단 기록 <span class="muted small">정규리그, 출전 선수</span></h2>
        <SortableTable v-if="data.roster.length" :columns="ROSTER_COLS" :rows="data.roster" :row-key="(p) => p.player.code"
                       :initial-sort="{ key: 'points', dir: 'desc' }" caption="선수단 시즌 기록">
          <template #cell-name="{ row }">
            <RouterLink :to="`/players/${row.player.code}?season=${data.season.code}`">{{ row.player.name }}</RouterLink>
          </template>
        </SortableTable>
        <p v-if="!data.roster.length" class="empty">이 시즌 출전 기록이 없습니다.</p>
      </section>

      <RelatedArticles :team="code" />
    </template>
  </AsyncState>
</template>

<style scoped>
.form-list { list-style: none; padding: 0; margin: 0; }
.form-list li { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; padding: 7px 0; border-bottom: 1px solid var(--color-border-soft); }
.form-list .who { font-weight: 700; min-width: 72px; }
.chg { font-variant-numeric: tabular-nums; }
.chg.up { color: var(--color-good); }
.chg.down { color: var(--color-bad); }
.note { margin: var(--space-2) 0 0; }
.title-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.title-row h1 { margin-bottom: 4px; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.title-row p { margin: 0 0 16px; }
.record { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
.record div { display: flex; flex-direction: column; }
.record b { font-size: 1.25rem; }
.form { display: flex; gap: 3px; }
.stack { display: grid; gap: 8px; }
.mt { margin-top: 16px; }
</style>
