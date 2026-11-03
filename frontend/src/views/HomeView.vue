<script setup>
// WEB-01: today's games (or the next ones), latest results, standings of both divisions.
import { onMounted, ref } from 'vue'
import { getMatches, getStandings } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import MatchCard from '@/components/MatchCard.vue'
import { GENDERS } from '@/constants/volley'
import { useFiltersStore } from '@/stores/filters'
import { fmtFullDate, postseasonZone, todayKst } from '@/utils/format'

const filters = useFiltersStore()
const state = ref({ loading: true, error: null })
const today = ref([])
const upcoming = ref([])
const recent = ref([])
const standings = ref({})
const latestSeason = ref(null)

onMounted(async () => {
  try {
    await filters.init()
    latestSeason.value = filters.seasons[0]
    const [t, r] = await Promise.all([
      getMatches({ date: todayKst(), page_size: 20 }),
      getMatches({ status: 'finished', order: 'desc', page_size: 6 }),
    ])
    today.value = t.results
    recent.value = r.results
    if (!today.value.length) upcoming.value = (await getMatches({ status: 'scheduled', page_size: 4 })).results
    const [m, f] = await Promise.all(['M', 'F'].map((g) => getStandings({ season: latestSeason.value?.code, gender: g })))
    standings.value = { M: m, F: f }
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
  }
})
</script>

<template>
  <AsyncState :loading="state.loading" :error="state.error">
    <section class="card">
      <div class="card-head">
        <h2>{{ today.length ? '오늘 경기' : '다음 경기' }}</h2>
        <span class="muted small">{{ fmtFullDate(new Date().toISOString()) }}</span>
      </div>
      <div class="match-grid" v-if="today.length || upcoming.length">
        <MatchCard v-for="m in (today.length ? today : upcoming)" :key="m.key" :match="m" :show-date="!today.length" />
      </div>
      <p v-else class="empty">예정된 경기가 없습니다.</p>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>최근 결과</h2>
        <RouterLink to="/schedule" class="small">전체 일정 →</RouterLink>
      </div>
      <div class="match-grid">
        <MatchCard v-for="m in recent" :key="m.key" :match="m" show-date />
      </div>
    </section>

    <div class="grid-2">
      <section v-for="g in ['M', 'F']" :key="g" class="card">
        <div class="card-head">
          <h2>{{ GENDERS[g] }} 순위</h2>
          <span class="muted small">{{ latestSeason?.years }} 정규리그</span>
        </div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>순위</th><th class="left">팀</th><th>경기</th><th>승</th><th>패</th><th>승점</th></tr></thead>
            <tbody>
              <tr v-for="r in standings[g]?.rows || []" :key="r.team.code" :class="postseasonZone(standings[g]?.rows)[r.team.code]">
                <td class="rank">{{ r.rank }}</td>
                <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
                <td>{{ r.games }}</td><td>{{ r.wins }}</td><td>{{ r.losses }}</td><td class="strong">{{ r.points }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <RouterLink :to="{ path: '/standings', query: { gender: g } }" class="small more">순위 자세히 →</RouterLink>
      </section>
    </div>
  </AsyncState>
</template>

<style scoped>
.match-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.more { display: inline-block; margin-top: 8px; }
.rank { font-weight: 700; border-left: 3px solid transparent; }
tr.po .rank { border-left-color: var(--color-primary); }
tr.semi .rank { border-left-color: var(--color-accent); }
</style>
