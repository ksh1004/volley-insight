<script setup>
// WEB-02 일정 화면: 달별 일정과 결과, 시즌·남녀부·팀 필터.
// 모션: 달 선택 강조가 미끄러지고, 새 달 목록이 도착할 때까지 이전 목록을 흐리게 남겨 두었다가 새 날짜들이 떠오르며
// 나타난다. "오늘 경기로" 버튼은 목록에 오늘 경기가 있으면 그곳으로 스크롤한다.
// 보기 두 가지(WEB-02 "월별 캘린더/목록"): 목록 = 날짜별 묶음을 격자로, 달력 = 월 달력(ScheduleCalendar).
// 보기 방식은 주소(?view=calendar)에 담아, 링크를 공유하면 같은 보기로 열린다.
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
// 시즌의 달 목록(달 선택 탭)
const months = computed(() => seasonMonths(filters.current?.start_date, filters.current?.end_date))
const month = ref('')
const team = ref('')
const teams = ref([])
const matches = ref([])
const state = ref({ loading: true, error: null })
const listKey = ref('') // 새 목록이 도착하면 바뀌는 키: 목록 전체가 한꺼번에 새로 들어오는 효과를 위해
const route = useRoute()
const view = ref(route.query.view === 'calendar' ? 'calendar' : 'list')
// 주소만 제자리에서 바꾼다(history.replaceState): 라우터로 이동하면 화면이 통째로 다시 만들어진다
// (App.vue가 전체 주소를 화면의 키로 쓰기 때문). 그러면 스크롤·애니메이션이 처음부터 다시 시작된다
watch(view, (v) => {
  const url = new URL(window.location.href)
  if (v === 'calendar') url.searchParams.set('view', 'calendar')
  else url.searchParams.delete('view')
  window.history.replaceState(window.history.state, '', url)
})

// 기본 달: 이번 달이 시즌 안이면 이번 달, 아니면 시즌 첫 달
function defaultMonth() {
  const now = todayKst().slice(0, 7)
  return months.value.includes(now) ? now : months.value[0] || ''
}

// 팀 필터 목록(시즌·남녀부가 바뀌면 다시). 고른 팀이 목록에 없으면 필터를 푼다
async function loadTeams() {
  teams.value = await getTeams({ season: filters.season, gender: filters.gender })
  if (!teams.value.some((t) => t.code === team.value)) team.value = ''
}

let requestNo = 0 // 필터를 빠르게 여러 번 바꿀 수 있으므로, 가장 마지막 요청의 응답만 화면에 반영한다
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

// 경기를 날짜별로 묶는다(서버가 시각순으로 주므로 앞에서부터 이어 붙이면 됨)
const byDay = computed(() => {
  const groups = []
  for (const m of matches.value) {
    const k = dayKey(m.scheduled_at)
    if (!groups.length || groups[groups.length - 1].key !== k) groups.push({ key: k, date: m.scheduled_at, items: [] })
    groups[groups.length - 1].items.push(m)
  }
  return groups
})

// '오늘 경기로' 버튼: 목록 보기이고 오늘 경기가 있을 때만
const todayKey = dayKey(new Date().toISOString())
const hasToday = computed(() => view.value === 'list' && byDay.value.some((g) => g.key === todayKey))
const todayDate = todayKst()
// 오늘 날짜 묶음으로 스크롤하고 포커스(키보드 사용자도 바로 이어서 읽을 수 있게)
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
// 시즌이 바뀌면 기본 달로, 남녀부만 바뀌면 달은 그대로. 팀 목록과 경기를 다시 불러온다
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
