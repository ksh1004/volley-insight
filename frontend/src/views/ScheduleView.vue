<script setup>
// WEB-02: schedule and results by month, with season / division / team filters.
import { computed, onMounted, ref, watch } from 'vue'
import { getMatches, getTeams } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FilterBar from '@/components/FilterBar.vue'
import MatchCard from '@/components/MatchCard.vue'
import { useFiltersStore } from '@/stores/filters'
import { dayKey, fmtDate, seasonMonths, todayKst } from '@/utils/format'

const filters = useFiltersStore()
const months = computed(() => seasonMonths(filters.current?.start_date, filters.current?.end_date))
const month = ref('')
const team = ref('')
const teams = ref([])
const matches = ref([])
const state = ref({ loading: true, error: null })

function defaultMonth() {
  const now = todayKst().slice(0, 7)
  return months.value.includes(now) ? now : months.value[0] || ''
}

async function loadTeams() {
  teams.value = await getTeams({ season: filters.season, gender: filters.gender })
  if (!teams.value.some((t) => t.code === team.value)) team.value = ''
}

let requestNo = 0 // filters can change quickly; only the latest response is shown
async function load() {
  const mine = ++requestNo
  state.value = { loading: true, error: null }
  try {
    const data = await getMatches({ season: filters.season, gender: filters.gender, team: team.value,
                                    month: month.value, page_size: 300 })
    if (mine === requestNo) matches.value = data.results
  } catch (e) {
    if (mine === requestNo) state.value.error = e.message
  } finally {
    if (mine === requestNo) state.value.loading = false
  }
}

const byDay = computed(() => {
  const groups = []
  for (const m of matches.value) {
    const k = dayKey(m.scheduled_at)
    if (!groups.length || groups[groups.length - 1].key !== k) groups.push({ key: k, date: m.scheduled_at, items: [] })
    groups[groups.length - 1].items.push(m)
  }
  return groups
})

onMounted(async () => {
  await filters.init()
  month.value = defaultMonth()
  await loadTeams()
  await load()
})
watch(() => [filters.season, filters.gender], async ([season], [oldSeason]) => {
  if (season !== oldSeason) month.value = defaultMonth()
  await loadTeams()
  await load()
})
watch([month, team], load)
</script>

<template>
  <h1>경기 일정·결과</h1>
  <FilterBar all-genders>
    <select v-model="team" aria-label="팀">
      <option value="">전체 팀</option>
      <option v-for="t in teams" :key="t.code" :value="t.code">{{ t.name }}</option>
    </select>
  </FilterBar>
  <div class="tabs months">
    <button v-for="mo in months" :key="mo" class="tab" :class="{ active: mo === month }" @click="month = mo">
      {{ Number(mo.slice(5)) }}월
    </button>
  </div>
  <AsyncState :loading="state.loading" :error="state.error" :empty="!matches.length" empty-text="이 달에는 경기가 없습니다.">
    <section v-for="g in byDay" :key="g.key" class="day">
      <h2 class="day-title">{{ fmtDate(g.date) }}</h2>
      <div class="day-grid">
        <MatchCard v-for="m in g.items" :key="m.key" :match="m" />
      </div>
    </section>
  </AsyncState>
</template>

<style scoped>
.months { margin-bottom: 20px; }
.day { margin-bottom: 20px; }
.day-title { font-size: .95rem; color: var(--color-muted-foreground); margin-bottom: 8px; }
.day-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
</style>
