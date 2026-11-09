<script setup>
// WEB-02: schedule and results by month, with season / division / team filters.
// Motion: the month highlight slides; a new month's days rise in while the old list stays (dimmed) until it arrives;
// "오늘 경기로" scrolls to today's games when they are in the list.
// Two views of the month (WEB-02 "월별 캘린더/목록"): list = day groups packed in a grid; calendar = month grid
// (ScheduleCalendar). The view is kept in the address (?view=calendar) so a shared link opens the same view.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import ScheduleCalendar from '@/components/ScheduleCalendar.vue'
import { reducedMotion } from '@/motion/core'
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
const listKey = ref('') // changes when a new list has arrived, so it enters as a whole
const route = useRoute()
const view = ref(route.query.view === 'calendar' ? 'calendar' : 'list')
// the address changes in place: a router navigation would remount the page (App.vue keys pages by full path)
watch(view, (v) => {
  const url = new URL(window.location.href)
  if (v === 'calendar') url.searchParams.set('view', 'calendar')
  else url.searchParams.delete('view')
  window.history.replaceState(window.history.state, '', url)
})

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
    if (mine === requestNo) {
      matches.value = data.results
      listKey.value = `${filters.season}-${filters.gender}-${team.value}-${month.value}`
    }
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

const todayKey = dayKey(new Date().toISOString())
const hasToday = computed(() => view.value === 'list' && byDay.value.some((g) => g.key === todayKey))
const todayDate = todayKst()
async function goToday() {
  await nextTick()
  const el = document.getElementById('day-today')
  el?.scrollIntoView({ behavior: reducedMotion.value ? 'auto' : 'smooth', block: 'start' })
  el?.focus({ preventScroll: true })
}

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
  <div class="month-row">
    <div v-glider class="tabs months" role="group" aria-label="월">
      <button v-for="mo in months" :key="mo" type="button" class="tab" :class="{ active: mo === month }" :aria-pressed="mo === month"
              @click="month = mo">
        {{ Number(mo.slice(5)) }}월
      </button>
    </div>
    <div class="right">
      <button v-if="hasToday" type="button" class="today-btn" @click="goToday">오늘 경기로 ↓</button>
      <div v-glider class="tabs" role="group" aria-label="보기 방식">
        <button type="button" class="tab" :aria-pressed="view === 'list'" @click="view = 'list'">목록</button>
        <button type="button" class="tab" :aria-pressed="view === 'calendar'" @click="view = 'calendar'">달력</button>
      </div>
    </div>
  </div>
  <AsyncState :loading="state.loading && !matches.length" :error="state.error" :empty="!state.loading && !matches.length"
              empty-text="이 달에는 경기가 없습니다.">
    <Transition name="swap" mode="out-in">
      <div v-if="view === 'calendar'" :key="`cal-${listKey}`" :class="{ busy: state.loading }" :aria-busy="state.loading">
        <ScheduleCalendar :month="month" :matches="matches" :today="todayDate" :show-gender="!filters.gender" />
      </div>
      <div v-else :key="listKey" class="days" :class="{ busy: state.loading }" :aria-busy="state.loading">
        <section v-for="(g, i) in byDay" :id="g.key === todayKey ? 'day-today' : undefined" :key="g.key" v-reveal="i" class="day"
                 :class="{ today: g.key === todayKey }" :tabindex="g.key === todayKey ? -1 : undefined">
          <h2 class="day-title">{{ fmtDate(g.date) }}<span v-if="g.key === todayKey" class="badge today">오늘</span></h2>
          <div class="day-grid">
            <MatchCard v-for="m in g.items" :key="m.key" :match="m" />
          </div>
        </section>
      </div>
    </Transition>
  </AsyncState>
</template>

<style scoped>
.month-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
.months { margin: 0; }
.right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.right .tabs { margin: 0; }
/* list: day groups packed side by side (a day with one game no longer takes a whole row) */
.days { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 4px 12px; align-items: start; }
.days .day-grid { grid-template-columns: 1fr; }
.today-btn { font: inherit; font-size: .85rem; font-weight: 700; color: var(--color-accent); background: var(--color-accent-soft);
             border: 1px solid transparent; border-radius: 999px; padding: 4px 12px; min-height: 34px; cursor: pointer;
             transition: border-color var(--dur-fast) var(--ease-out); }
.today-btn:hover { border-color: var(--color-accent); }
@media (pointer: coarse) { .today-btn { min-height: 44px; } }
.day { margin-bottom: 20px; scroll-margin-top: 72px; outline: none; }
.day-title { font-size: .95rem; color: var(--color-muted-foreground); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.day.today .day-title { color: var(--color-accent); }
.day-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
</style>
