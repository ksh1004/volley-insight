<script setup>
// WEB-02 일정 화면의 월 달력: 그 달의 모든 날과 날마다의 경기(예정이면 시각, 끝났으면 결과). 날을 고르면 달력 아래에
// 그날의 경기 카드가 나온다. 키보드: 달력 전체가 탭 한 번에 들어가고(roving tabindex), 화살표로 날을 옮기며
// Enter/Space로 고른다. 휴대폰에서는 날짜와 경기마다 점 하나만 보이고, 자세한 것은 아래 목록이 보여 준다.
// 모션: 고른 날의 경기 목록이 바뀌어 들어오고, 고른 칸의 테두리와 '오늘' 표시는 CSS 전환으로만 움직인다.
import { computed, nextTick, ref, watch } from 'vue'
import MatchCard from '@/components/MatchCard.vue'
import { GENDERS } from '@/constants/volley'
import { fmtTime } from '@/utils/format'

const props = defineProps({
  month: { type: String, required: true }, // YYYY-MM
  matches: { type: Array, required: true },
  today: { type: String, default: '' }, // 오늘 YYYY-MM-DD(서울)
  showGender: Boolean, // 남녀부 경기가 섞여 있어 성별 표시가 필요할 때
})

const WEEK = ['일', '월', '화', '수', '목', '금', '토']
// 경기 시각 → 서울 날짜 "YYYY-MM-DD"
const kstDate = (iso) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date(iso))
const done = (m) => ['FINISHED', 'CONFIRMED'].includes(m.status)

// 날짜 → 그날 경기들
const byDate = computed(() => {
  const out = {}
  for (const m of props.matches) (out[kstDate(m.scheduled_at)] ||= []).push(m)
  return out
})
// 달력 칸들: 1일 앞의 빈칸(요일 맞추기) + 날마다 한 칸
const cells = computed(() => {
  const [y, mo] = props.month.split('-').map(Number)
  const lead = new Date(Date.UTC(y, mo - 1, 1)).getUTCDay() // 1일의 요일(시간대 영향 없이 날짜만으로 계산)
  const days = new Date(Date.UTC(y, mo, 0)).getUTCDate() // 그 달의 날 수(다음 달 0일 = 이번 달 마지막 날)
  const out = Array.from({ length: lead }, (_, i) => ({ blank: true, key: `b${i}` }))
  for (let d = 1; d <= days; d++) {
    const date = `${props.month}-${String(d).padStart(2, '0')}`
    out.push({ date, d, weekday: (lead + d - 1) % 7, games: byDate.value[date] || [] })
  }
  return out
})
const days = computed(() => cells.value.filter((c) => !c.blank))

// ---- 고른 날: 오늘 경기가 있으면 오늘, 아니면 경기가 있는 첫날
const picked = ref('')
function defaultPick() {
  const withGames = days.value.filter((c) => c.games.length)
  picked.value = withGames.find((c) => c.date === props.today)?.date || withGames[0]?.date || days.value[0]?.date || ''
}
watch(() => [props.month, props.matches], defaultPick, { immediate: true })
const pickedDay = computed(() => days.value.find((c) => c.date === picked.value))

// ---- 키보드: 탭 한 번으로 들어오고, ←→ 하루, ↑↓ 일주일, Home/End 그 달의 처음·끝으로 포커스(와 선택)를 옮긴다
const grid = ref(null)
async function move(e, cell) {
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 7, ArrowUp: -7, Home: -(cell.d - 1), End: days.value.length - cell.d }[e.key]
  if (step == null) return
  e.preventDefault()
  const next = days.value[Math.min(days.value.length - 1, Math.max(0, cell.d - 1 + step))]
  picked.value = next.date
  await nextTick()
  grid.value?.querySelector(`[data-date="${next.date}"]`)?.focus()
}

// 칸마다 화면 낭독기가 읽을 설명(날짜, 요일, 오늘 여부, 경기들)
const label = (c) => {
  const head = `${Number(props.month.slice(5))}월 ${c.d}일 ${WEEK[c.weekday]}요일${c.date === props.today ? ', 오늘' : ''}`
  if (!c.games.length) return `${head}, 경기 없음`
  return `${head}, 경기 ${c.games.length}개: ${c.games.map((m) => `${m.home.name} 대 ${m.away.name} ${done(m) ? `${m.home_sets} 대 ${m.away_sets}` : fmtTime(m.scheduled_at)}`).join(', ')}`
}
</script>

<template>
  <div class="cal">
    <div ref="grid" class="grid" role="group" :aria-label="`${Number(month.slice(5))}월 경기 달력`">
      <span v-for="(w, i) in WEEK" :key="w" class="wd" :class="{ sun: i === 0, sat: i === 6 }" aria-hidden="true">{{ w }}</span>
      <template v-for="c in cells" :key="c.key || c.date">
        <span v-if="c.blank" class="cell blank" aria-hidden="true" />
        <button v-else type="button" class="cell" :data-date="c.date"
                :class="{ has: c.games.length, today: c.date === today, on: c.date === picked, sun: c.weekday === 0, sat: c.weekday === 6 }"
                :tabindex="c.date === picked ? 0 : -1" :aria-pressed="c.date === picked" :aria-label="label(c)"
                @click="picked = c.date" @keydown="move($event, c)">
          <span class="d">{{ c.d }}<span v-if="c.date === today" class="today-tag">오늘</span></span>
          <span class="evs" aria-hidden="true">
            <span v-for="m in c.games.slice(0, 3)" :key="m.key" class="ev" :class="{ done: done(m), live: m.status === 'LIVE' }">
              <b>{{ done(m) ? `${m.home_sets}:${m.away_sets}` : m.status === 'LIVE' ? '진행' : fmtTime(m.scheduled_at) }}</b>
              <template v-if="showGender">{{ GENDERS[m.gender].slice(0, 1) }} </template>{{ m.home.name }} · {{ m.away.name }}
            </span>
            <span v-if="c.games.length > 3" class="more">+{{ c.games.length - 3 }}</span>
          </span>
          <span class="dots" aria-hidden="true"><i v-for="m in c.games" :key="m.key" :class="{ done: done(m) }" /></span>
        </button>
      </template>
    </div>

    <Transition name="swap" mode="out-in">
      <section v-if="pickedDay" :key="picked" class="picked" aria-live="polite">
        <h2 class="picked-title">{{ Number(month.slice(5)) }}월 {{ pickedDay.d }}일 ({{ WEEK[pickedDay.weekday] }})
          <span v-if="pickedDay.date === today" class="badge today">오늘</span></h2>
        <div v-if="pickedDay.games.length" class="picked-grid">
          <MatchCard v-for="m in pickedDay.games" :key="m.key" :match="m" />
        </div>
        <p v-else class="empty small">이날은 경기가 없습니다.</p>
      </section>
    </Transition>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.wd { text-align: center; font-size: .78rem; font-weight: 700; color: var(--color-muted-foreground); padding: 4px 0; }
.wd.sun, .cell.sun .d { color: var(--color-bad); }
.wd.sat, .cell.sat .d { color: var(--color-primary); }
.cell { position: relative; display: flex; flex-direction: column; align-items: stretch; gap: 3px; min-height: 92px; padding: 6px;
        text-align: left; font: inherit; color: var(--color-foreground); background: var(--color-card); cursor: pointer;
        border: 1px solid var(--color-border); border-radius: 8px;
        transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out),
                    background-color var(--dur-fast) var(--ease-out); }
.cell.blank { background: transparent; border-color: transparent; cursor: default; }
.cell:not(.has) { background: var(--color-muted); color: var(--color-muted-foreground); }
.cell:hover { border-color: var(--color-primary); }
.cell.on { border-color: var(--color-primary); box-shadow: 0 0 0 2px var(--color-primary) inset; }
.cell.today { background: var(--color-accent-soft); }
.d { font-size: .8rem; font-weight: 700; font-variant-numeric: tabular-nums; display: flex; align-items: center; gap: 4px; }
.today-tag { font-size: .65rem; color: var(--color-accent); font-weight: 800; }
.evs { display: grid; gap: 2px; min-width: 0; }
.ev { font-size: .72rem; line-height: 1.3; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
      color: var(--color-foreground); }
.ev b { font-variant-numeric: tabular-nums; color: var(--color-muted-foreground); margin-right: 2px; }
.ev.done b { color: var(--color-primary); }
.ev.live b { color: var(--color-accent); }
.more { font-size: .7rem; color: var(--color-muted-foreground); }
.dots { display: none; gap: 3px; flex-wrap: wrap; }
.dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--color-muted-foreground); }
.dots i.done { background: var(--color-primary); }
.picked { margin-top: var(--space-4); }
.picked-title { font-size: .95rem; display: flex; align-items: center; gap: 8px; }
.picked-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
@media (max-width: 640px) {
  .cell { min-height: 52px; padding: 4px; align-items: center; }
  .evs { display: none; }
  .dots { display: flex; justify-content: center; }
  .today-tag { display: none; }
  .picked-grid { grid-template-columns: 1fr; }
}
</style>
