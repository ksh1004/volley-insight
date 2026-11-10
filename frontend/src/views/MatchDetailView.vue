<script setup>
// WEB-03 경기 화면: 스코어보드, 세트별 점수, 경기 프리뷰(예측·상대 전적), 두 팀 기록 비교, 선수 기록(박스스코어),
// 세트별 점수 흐름 차트와 문자중계. 데이터 문제가 있는 경기는 스코어보드 아래에 안내를 띄운다(PIPE-10).
// 경기 데이터는 /api/matches/{key} 한 번, 문자중계는 세트를 고를 때마다 /rallies?set=N 으로 받는다.
import { computed, onMounted, ref, watch } from 'vue'
import { getMatch, getRallies } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import CompareBars from '@/components/CompareBars.vue'
import MatchPreview from '@/components/MatchPreview.vue'
import ScoreFlow from '@/components/ScoreFlow.vue'
import SortableTable from '@/components/SortableTable.vue'
import { ACTIONS, GENDERS, NON_PLAY, POSITIONS, RESULTS, STATUS } from '@/constants/volley'
import RollingNumber from '@/motion/RollingNumber.vue'
import { fmtFullDate, fmtTime, num, pct } from '@/utils/format'

const props = defineProps({ matchKey: { type: String, required: true } })
const match = ref(null)
const state = ref({ loading: true, error: null })
const side = ref('home') // 선수 기록 표에서 보는 팀
const setNo = ref(1) // 문자중계·점수 흐름에서 보는 세트
const rallies = ref(null)
const rallyState = ref({ loading: false, error: null })

const done = computed(() => ['FINISHED', 'CONFIRMED'].includes(match.value?.status))
// 'home' | 'away' → 팀 정보, 그 팀이 이겼는가(끝난 경기만)
const team = (s) => match.value?.[s]
const won = (s) => done.value && (s === 'home'
  ? match.value.home_sets > match.value.away_sets : match.value.away_sets > match.value.home_sets)
// 제목 아래 한 줄: 시즌, 남녀부, 대회(라운드), 날짜와 시각
const subtitle = computed(() => {
  const m = match.value
  const round = m.league_code === '201' ? ` ${m.round}라운드` : ''
  return `${m.season_years} 시즌 ${GENDERS[m.gender]} · ${m.competition}${round} · ${fmtFullDate(m.scheduled_at)} ${fmtTime(m.scheduled_at)}`
})
// PIPE-10: 이 경기의 해결 안 된 데이터 문제마다 "3·4세트 문자중계 없음" 같은 안내 문구
const notes = computed(() => (match.value?.data_notes?.issues || [])
  .map((i) => (i.sets.length ? `${i.sets.join('·')}세트 ${i.label}` : i.label)))
// 모든 세트 점수의 합 [홈, 원정]
const totals = computed(() => match.value?.set_details.reduce((acc, s) => [acc[0] + s.home_points, acc[1] + s.away_points], [0, 0]))

// 두 팀 기록 비교 막대 행(CompareBars 형식)
const compareRows = computed(() => {
  const h = match.value?.team_stats.home
  const a = match.value?.team_stats.away
  if (!h || !a) return []
  return [
    { label: '득점', a: h.points, b: a.points },
    { label: '공격 성공률', a: h.attack_pct, b: a.attack_pct, fmt: pct },
    { label: '블로킹', a: h.block_success, b: a.block_success },
    { label: '서브 에이스', a: h.serve_aces, b: a.serve_aces },
    { label: '리시브 효율', a: h.receive_efficiency, b: a.receive_efficiency, fmt: pct },
    { label: '디그', a: h.dig_success, b: a.dig_success },
    { label: '범실', a: h.errors, b: a.errors, lowerIsBetter: true },
    { label: '상대 범실', a: h.opponent_errors, b: a.opponent_errors },
  ]
})

// 선수 기록 표의 열(정렬 가능. 디자인 시스템: 정렬 표는 aria-sort로 상태를 알린다).
// ratioSort: '성공/시도' 열을 비율로 정렬하는 함수를 만든다
const ratioSort = (s, a) => (row) => (row[a] ? row[s] / row[a] : null)
const BOX_COLS = [
  { key: 'name', label: '선수', left: true, sticky: true, get: (p) => p.player.name },
  { key: 'position', label: '포지션', left: true, get: (p) => POSITIONS[p.position] || p.position || null },
  { key: 'sets_played', label: '출전 세트' },
  { key: 'points', label: '득점', strong: true },
  { key: 'attack', label: '공격 성공/시도', get: ratioSort('attack_success', 'attack_attempts'), fmt: (p) => `${p.attack_success}/${p.attack_attempts}` },
  { key: 'attack_pct', label: '공격 성공률', fmt: (p) => pct(p.attack_pct) },
  { key: 'block_success', label: '블로킹' },
  { key: 'serve_aces', label: '서브 에이스' },
  { key: 'receive', label: '리시브 정확/시도', get: (p) => p.receive_attempts || null, fmt: (p) => `${p.receive_excellent}/${p.receive_attempts}` },
  { key: 'receive_efficiency', label: '리시브 효율', fmt: (p) => pct(p.receive_efficiency) },
  { key: 'dig_success', label: '디그' },
  { key: 'set_success', label: '세트' },
  { key: 'errors', label: '범실' },
]

// 문자중계를 랠리 단위로 묶는다(rally_no 하나 = 랠리 하나, 또는 교체·작전 타임).
// scorer: 그 랠리의 득점 이벤트, note: 랠리가 아닌 안내(교체 등), score: 랠리가 끝난 뒤 점수
const groups = computed(() => {
  const out = []
  for (const e of rallies.value?.events || []) {
    const last = out[out.length - 1]
    if (last && last.rally_no === e.rally_no) last.events.push(e)
    else out.push({ rally_no: e.rally_no, events: [e] })
  }
  return out.map((g) => {
    const scorer = g.events.find((e) => e.grade > 0)
    const end = g.events[g.events.length - 1]
    return { ...g, scorer, note: g.events.every((e) => NON_PLAY.has(e.action)), score: `${end.home_score}:${end.away_score}` }
  })
})
// 팀 코드 → 'home' | 'away'
const sideOf = (code) => (code === match.value?.home.code ? 'home' : code === match.value?.away.code ? 'away' : '')
// 이벤트 한 줄 설명: "선수 이름 동작 결과"(예: "김연경 오픈 성공")
const describe = (e) => {
  const who = e.player?.name || (e.team ? team(sideOf(e.team))?.name : '')
  const what = ACTIONS[e.action] || e.action
  const result = RESULTS[e.result] ?? e.result
  return `${who ? `${who} ` : ''}${what}${result ? ` ${result}` : ''}`
}

// 고른 세트의 문자중계 불러오기(그 세트에 문자중계가 없으면 비움)
async function loadRallies() {
  if (!match.value?.rally_sets.includes(setNo.value)) { rallies.value = null; return }
  rallyState.value = { loading: true, error: null }
  try {
    rallies.value = await getRallies(props.matchKey, setNo.value)
  } catch (e) {
    rallyState.value.error = e.message
  } finally {
    rallyState.value.loading = false
  }
}

// 경기 데이터를 받고, 문자중계가 있는 첫 세트를 골라 불러온다
onMounted(async () => {
  try {
    match.value = await getMatch(props.matchKey)
    setNo.value = match.value.rally_sets[0] || 1
    await loadRallies()
  } catch (e) {
    state.value.error = e.response?.status === 404 ? '경기를 찾을 수 없습니다.' : e.message
  } finally {
    state.value.loading = false
  }
})
watch(setNo, loadRallies)
</script>

<template>
  <AsyncState :loading="state.loading" :error="state.error">
    <template v-if="match">
      <section class="card scoreboard">
        <div class="muted small center">
          {{ subtitle }}
        </div>
        <div class="board">
          <RouterLink :to="`/teams/${match.home.code}`" class="board-team" :class="{ won: won('home') }">
            <span class="side muted small">홈</span>{{ match.home.name }}
          </RouterLink>
          <div class="board-score" v-if="done">
            <b :class="{ won: won('home') }"><RollingNumber :value="match.home_sets" roll-in /></b><span>:</span><b :class="{ won: won('away') }"><RollingNumber :value="match.away_sets" roll-in /></b>
          </div>
          <div class="board-score status" v-else>{{ STATUS[match.status] }}</div>
          <RouterLink :to="`/teams/${match.away.code}`" class="board-team" :class="{ won: won('away') }">
            <span class="side muted small">원정</span>{{ match.away.name }}
          </RouterLink>
        </div>

        <div class="table-wrap" v-if="match.set_details.length">
          <table class="data sets">
            <thead><tr><th class="left">팀</th><th v-for="s in match.set_details" :key="s.set_no">{{ s.set_no }}세트</th><th>합계</th></tr></thead>
            <tbody>
              <tr v-for="s in ['home', 'away']" :key="s">
                <td class="left">{{ match[s].name }}</td>
                <td v-for="d in match.set_details" :key="d.set_no"
                    :class="{ strong: d[`${s}_points`] > d[`${s === 'home' ? 'away' : 'home'}_points`] }">{{ d[`${s}_points`] }}</td>
                <td>{{ totals[s === 'home' ? 0 : 1] }}</td>
              </tr>
              <tr class="muted small"><td class="left">시간(분)</td><td v-for="d in match.set_details" :key="d.set_no">{{ d.duration_min ?? '-' }}</td><td>{{ match.duration_min ?? '-' }}</td></tr>
            </tbody>
          </table>
        </div>
        <p v-if="match.live || match.status === 'LIVE'" class="live-link">
          <RouterLink :to="`/matches/${match.key}/live`">
            {{ match.live?.mode === 'REPLAY' ? '재생 화면 보기' : match.status === 'LIVE' ? '실시간 중계 보기' : '실시간 기록 보기' }} →
          </RouterLink>
        </p>
        <ul class="facts small muted">
          <li>장소 {{ match.venue || '-' }}</li>
          <li v-if="match.spectators != null">관중 {{ num(match.spectators) }}명</li>
          <li v-if="match.referee_main">주심 {{ match.referee_main }}<span v-if="match.referee_sub"> · 부심 {{ match.referee_sub }}</span></li>
          <li v-if="match.broadcasters">중계 {{ match.broadcasters.replaceAll(',', ', ') }}</li>
        </ul>
        <div v-if="notes.length || match.data_notes?.corrected" class="data-note small" role="note">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.5" /></svg>
          <div>
            <p v-if="notes.length">KOVO 공식 기록에 빠지거나 맞지 않는 부분이 있습니다: {{ notes.join(', ') }}. 원본을 그대로 보여 드립니다.</p>
            <p v-if="match.data_notes?.corrected">이 경기 기록 중 {{ match.data_notes.corrected }}개 항목은 관리자가 근거를 확인해 고쳤습니다.</p>
          </div>
        </div>
      </section>

      <MatchPreview v-if="!done || match.preview?.prediction" :match="match" />

      <template v-if="done">
        <section v-reveal class="card" v-if="compareRows.length">
          <h2>팀 기록 비교</h2>
          <CompareBars :rows="compareRows" :a-label="match.home.name" :b-label="match.away.name" />
        </section>
        <p v-else class="card empty">선수·팀 기록이 아직 수집되지 않았습니다.</p>

        <section v-reveal class="card" v-if="match.players.home.length || match.players.away.length">
          <div class="card-head">
            <h2>선수 기록</h2>
            <div v-glider class="tabs" role="group" aria-label="팀 선택">
              <button v-for="s in ['home', 'away']" :key="s" type="button" class="tab" :aria-pressed="side === s" @click="side = s">{{ match[s].name }}</button>
            </div>
          </div>
          <SortableTable :columns="BOX_COLS" :rows="match.players[side]" :row-key="(p) => p.player.code"
                         :initial-sort="{ key: 'points', dir: 'desc' }" :caption="`${match[side].name} 선수 기록`">
            <template #cell-name="{ row }">
              <RouterLink :to="`/players/${row.player.code}`">{{ row.player.name }}</RouterLink>
              <span class="muted small no" v-if="row.back_number">{{ row.back_number }}</span>
            </template>
          </SortableTable>
        </section>

        <section v-reveal class="card">
          <div class="card-head">
            <h2>세트별 흐름</h2>
            <div v-glider class="tabs" role="group" aria-label="세트 선택">
              <button v-for="s in match.set_details" :key="s.set_no" type="button" class="tab" :aria-pressed="setNo === s.set_no"
                      @click="setNo = s.set_no">{{ s.set_no }}세트 <span class="set-score">{{ s.home_points }}:{{ s.away_points }}</span></button>
            </div>
          </div>
          <p v-if="!match.rally_sets.includes(setNo)" class="empty">
            이 세트의 문자중계가 없습니다.<span v-if="match.season < '020'"> (KOVO 문자중계는 2023-24 시즌부터 제공)</span>
          </p>
          <AsyncState v-else :loading="rallyState.loading" :error="rallyState.error">
            <template v-if="rallies">
              <ScoreFlow :key="setNo" :events="rallies.events" :home="match.home.name" :away="match.away.name" />
              <details class="pbp">
                <summary>문자중계 보기 ({{ groups.filter((g) => g.scorer).length }}랠리)</summary>
                <ol class="rallies">
                  <li v-for="g in groups" :key="g.rally_no" :class="['rally', g.note ? 'note' : sideOf(g.scorer?.team)]">
                    <template v-if="g.note">
                      <span class="small muted">{{ g.events.map(describe).join(' · ') }}</span>
                    </template>
                    <template v-else>
                      <span class="score">{{ g.score }}</span>
                      <span v-if="g.scorer" class="who small">{{ team(sideOf(g.scorer.team))?.name }} 득점</span>
                      <span class="chain small">{{ g.events.map(describe).join(' → ') }}</span>
                    </template>
                  </li>
                </ol>
              </details>
            </template>
          </AsyncState>
        </section>
      </template>
      <p class="small muted">출처: <a :href="match.source.url" target="_blank" rel="noopener">{{ match.source.name }}</a></p>
    </template>
  </AsyncState>
</template>

<style scoped>
.center { text-align: center; }
.live-link { text-align: center; margin: 8px 0 0; font-weight: 700; }
.no { margin-left: 4px; }
.board { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px; margin: 16px 0; }
.board-team { font-size: 1.35rem; font-weight: 800; color: var(--color-foreground); display: flex; flex-direction: column; align-items: center; }
.board-team.won { color: var(--color-primary); }
.side { font-weight: 500; }
.board-score { font-size: 2.4rem; font-weight: 800; display: flex; gap: 10px; color: var(--color-muted-foreground); font-variant-numeric: tabular-nums; }
.board-score b.won { color: var(--color-primary); }
.board-score.status { font-size: 1rem; }
.sets td, .sets th { text-align: center; }
.facts { list-style: none; padding: 0; margin: 12px 0 0; display: flex; flex-wrap: wrap; gap: 4px 16px; justify-content: center; }
.data-note { display: flex; gap: 8px; align-items: flex-start; margin: 12px auto 0; max-width: 640px; padding: 8px 12px;
             border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-muted); color: var(--color-muted-foreground); }
.data-note p { margin: 0; }
.data-note p + p { margin-top: 2px; }
.data-note svg { flex: none; width: 16px; height: 16px; margin-top: 2px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
.pbp { margin-top: 12px; }
.pbp summary { cursor: pointer; font-weight: 600; color: var(--color-primary); }
.rallies { list-style: none; padding: 0; margin: 8px 0 0; max-height: 520px; overflow-y: auto; border-top: 1px solid var(--color-border-soft); }
.rally { display: flex; gap: 12px; padding: 6px 4px; border-bottom: 1px solid var(--color-border-soft); align-items: baseline; }
.rally .score { font-weight: 800; min-width: 48px; font-variant-numeric: tabular-nums; }
.rally.home .score { color: var(--color-home); }
.rally.away .score { color: var(--color-away); }
.rally.note { padding-left: 64px; }
.who { flex: none; min-width: 84px; font-weight: 600; }
.rally.home .who { color: var(--color-home); }
.rally.away .who { color: var(--color-away); }
.chain { color: var(--color-muted-foreground); }
.set-score { opacity: .75; font-size: .8em; font-variant-numeric: tabular-nums; }
.board-team .side { font-size: .75rem; }
@media (max-width: 560px) {
  .board-team { font-size: 1.05rem; }
  .board-score { font-size: 1.8rem; }
}
</style>
