<script setup>
// WEB-04: one game as it happens (or a replay of a finished game, clearly labelled).
// Data: SSE snapshot + updates from the gateway (useLiveStream). Layout follows design-system MASTER.md:
// dense cards, home = primary / away = accent, every colour paired with text, one atomic status line for
// screen readers (ui-ux-pro-max "Contextual Live Badge Updates": announce the score in context, not bare numbers).
import { computed, onBeforeUnmount, ref } from 'vue'
import AsyncState from '@/components/AsyncState.vue'
import CompareBars from '@/components/CompareBars.vue'
import ScoreFlow from '@/components/ScoreFlow.vue'
import WinProbBar from '@/components/WinProbBar.vue'
import { useLiveStream } from '@/composables/useLiveStream'
import { ACTIONS, GENDERS, LIVE_MODES, POINT_KINDS, RESULTS } from '@/constants/volley'
import { fmtFullDate, fmtTime, pct } from '@/utils/format'

const props = defineProps({ matchKey: { type: String, required: true } })
const { match, state, points, connection, error, lastAt } = useLiveStream(props.matchKey)

const now = ref(Date.now())
const timer = setInterval(() => { now.value = Date.now() }, 5000)
onBeforeUnmount(() => clearInterval(timer))

const loading = computed(() => !match.value && !error.value && connection.value !== 'unsupported')
const s = computed(() => state.value)
const name = (code) => (code === match.value?.home.code ? match.value.home.name : code === match.value?.away.code ? match.value.away.name : '')
const sideOf = (code) => (code === match.value?.home.code ? 'home' : 'away')
const isReplay = computed(() => s.value?.mode === 'REPLAY')
const finished = computed(() => s.value?.status === 'FINISHED')
const subtitle = computed(() => {
  const m = match.value
  const round = m.league_code === '201' ? ` ${m.round}라운드` : ''
  return `${GENDERS[m.gender]} · ${m.competition}${round} · ${fmtFullDate(m.scheduled_at)} ${fmtTime(m.scheduled_at)}`
})

const CONNECTION = { connecting: '연결 중', open: '실시간 연결됨', reconnecting: '다시 연결하는 중', closed: '연결 끊김',
                     unsupported: '이 브라우저는 실시간 갱신을 지원하지 않습니다' }
const age = computed(() => {
  if (!lastAt.value) return ''
  const sec = Math.max(0, Math.round((now.value - new Date(lastAt.value).getTime()) / 1000))
  return sec < 60 ? `${sec}초 전 수집` : sec < 3600 ? `${Math.floor(sec / 60)}분 전 수집` : `${Math.floor(sec / 3600)}시간 전 수집`
})

// ---- scoreboard
const setLabel = computed(() => {
  if (!s.value) return ''
  if (finished.value) return '경기 종료'
  if (!s.value.set_open) return s.value.current_set ? `${s.value.current_set}세트 종료 · 다음 세트 준비` : '경기 시작 전'
  return `${s.value.current_set}세트`
})
const run = computed(() => {
  if (!s.value || finished.value) return null
  const n = Math.max(s.value.home_run, s.value.away_run)
  if (n < 3) return null
  return `${name(s.value.home_run >= s.value.away_run ? s.value.home : s.value.away)} ${n}연속 득점`
})
const announce = computed(() => {
  if (!s.value) return ''
  const sets = `세트 스코어 ${s.value.home_sets} 대 ${s.value.away_sets}`
  if (finished.value) return `경기 종료. ${match.value.home.name} ${sets.replace('세트 스코어 ', '')} ${match.value.away.name}`
  const serve = s.value.serving ? `, ${name(s.value.serving)} 서브` : ''
  return `${s.value.current_set}세트 ${match.value.home.name} ${s.value.home_points} 대 ${s.value.away_points} ${match.value.away.name}${serve}. ${sets}`
})

// ---- set selection (follows the current set until the viewer picks one)
const picked = ref(null)
const setNumbers = computed(() => Array.from({ length: s.value?.current_set || 0 }, (_, i) => i + 1))
const shownSet = computed(() => picked.value ?? s.value?.current_set ?? 1)
const flowEvents = computed(() => points.value.filter((p) => p.set === shownSet.value)
  .map((p) => ({ grade: 1, home_score: p.home_score, away_score: p.away_score })))

const metricsView = ref('set') // set | match
const metricRows = computed(() => {
  const ms = (s.value?.metrics || []).filter((m) => metricsView.value === 'match' || m.set === shownSet.value)
  const sum = (side, f) => ms.filter((m) => m.side === side).reduce((acc, m) => acc + (m[f] || 0), 0)
  const rate = (side) => {
    const att = sum(side, 'attack_attempts')
    return att ? (100 * sum(side, 'attack_success')) / att : null
  }
  const n = (v) => String(v)
  return [
    { label: '득점', a: sum('home', 'points'), b: sum('away', 'points'), fmt: n },
    { label: '공격 성공률', a: rate('home'), b: rate('away'), fmt: pct },
    { label: '서브 에이스', a: sum('home', 'serve_aces'), b: sum('away', 'serve_aces'), fmt: n },
    { label: '블로킹 득점', a: sum('home', 'block_points'), b: sum('away', 'block_points'), fmt: n },
    { label: '범실', a: sum('home', 'errors'), b: sum('away', 'errors'), fmt: n, lowerIsBetter: true },
  ]
})

// ---- recent points, newest first
const recent = computed(() => points.value.slice(-12).reverse())
function describe(p) {
  if (p.kind === 'OPP_ERROR') {
    if (!p.player_name) return '상대 범실'
    const what = [ACTIONS[p.action], RESULTS[p.result]].filter(Boolean).join(' ')
    return `상대 범실 · ${p.player_name}${what ? ` ${what}` : ''}`
  }
  const how = p.kind === 'ATTACK' ? ACTIONS[p.action] || '공격' : POINT_KINDS[p.kind]
  return p.player_name ? `${p.player_name} ${how}` : how
}
</script>

<template>
  <div v-if="connection === 'unsupported'" class="card state">{{ CONNECTION.unsupported }}</div>
  <AsyncState v-else :loading="loading" :error="!match ? error : null" :rows="6">
    <template v-if="match">
      <p class="sr-only" role="status" aria-atomic="true">{{ announce }}</p>

      <section class="card board-card" :aria-busy="connection !== 'open'">
        <div class="top small">
          <span class="muted">{{ subtitle }}</span>
          <span class="chips">
            <span v-if="s" class="mode" :class="isReplay ? 'replay' : 'live'">
              <i v-if="!isReplay && !finished" class="dot" aria-hidden="true" />{{ LIVE_MODES[s.mode] }}
            </span>
            <span class="conn" :class="connection">{{ CONNECTION[connection] }}<template v-if="age"> · {{ age }}</template></span>
          </span>
        </div>

        <p v-if="isReplay" class="note small">
          종료된 경기의 문자중계를 {{ finished ? '' : '빠르게 ' }}다시 보여주는 <b>재생</b>입니다. 지금 진행 중인 경기가 아닙니다.
        </p>

        <template v-if="s">
          <div class="board">
            <RouterLink :to="`/teams/${match.home.code}`" class="team home">
              <span class="side small">홈</span>
              <span class="tname">{{ match.home.name }}</span>
              <span class="serve small" :class="{ on: s.serving === s.home }">
                <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" /><path d="M8 1.5c2 2 2.6 4.5 1.6 7.5M2 6.5c2.5-.8 5.5-.3 7.6 2.5M4 13c.5-2.6 2.6-4.4 5.6-4" /></svg>
                {{ s.serving === s.home ? '서브' : '' }}
              </span>
            </RouterLink>
            <div class="center">
              <div class="sets" :aria-label="`세트 스코어 ${s.home_sets} 대 ${s.away_sets}`">
                <b class="h">{{ s.home_sets }}</b><span>:</span><b class="a">{{ s.away_sets }}</b>
              </div>
              <div class="set-label small">{{ setLabel }}</div>
              <div v-if="!finished" class="points" :aria-label="`현재 세트 점수 ${s.home_points} 대 ${s.away_points}`">
                <b class="h">{{ s.home_points }}</b><span>:</span><b class="a">{{ s.away_points }}</b>
              </div>
            </div>
            <RouterLink :to="`/teams/${match.away.code}`" class="team away">
              <span class="side small">원정</span>
              <span class="tname">{{ match.away.name }}</span>
              <span class="serve small" :class="{ on: s.serving === s.away }">
                <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" /><path d="M8 1.5c2 2 2.6 4.5 1.6 7.5M2 6.5c2.5-.8 5.5-.3 7.6 2.5M4 13c.5-2.6 2.6-4.4 5.6-4" /></svg>
                {{ s.serving === s.away ? '서브' : '' }}
              </span>
            </RouterLink>
          </div>

          <div class="under">
            <span v-if="run" class="run small">{{ run }}</span>
            <ol v-if="s.set_scores.length" class="set-scores small" aria-label="끝난 세트 점수">
              <li v-for="([h, a], i) in s.set_scores" :key="i">
                <span class="muted">{{ i + 1 }}세트</span>
                <b :class="{ w: h > a }">{{ h }}</b>-<b :class="{ w: a > h }">{{ a }}</b>
              </li>
            </ol>
          </div>

          <div v-if="s.home_win_prob != null" class="prob">
            <WinProbBar :home="match.home.name" :away="match.away.name" :prob="s.home_win_prob" label="실시간 승리 확률" />
            <p class="muted small">
              실시간 승리 확률<template v-if="s.home_set_win_prob != null"> · 이번 세트 {{ match.home.name }}
                {{ Math.round(s.home_set_win_prob * 100) }}%</template>.
              현재 점수·서브권과 두 팀의 이번 시즌 사이드아웃·브레이크 비율로 계산합니다.
            </p>
          </div>

          <p v-if="finished" class="done small">
            경기가 끝났습니다.
            <RouterLink :to="`/matches/${match.key}`">경기 상세</RouterLink>에서 기록을 볼 수 있습니다
            <span class="muted">(공식 기록은 경기 후 수집되면 반영됩니다)</span>.
          </p>
        </template>

        <div v-else class="waiting">
          <p><b>아직 문자중계가 시작되지 않았습니다.</b></p>
          <p class="muted small">경기 시작 10분 전부터 KOVO 문자중계를 15초마다 확인합니다. 경기가 시작되면 이 화면이 저절로 바뀝니다.</p>
          <RouterLink :to="`/matches/${match.key}`" class="small">경기 프리뷰 보기 →</RouterLink>
        </div>
      </section>

      <template v-if="s && s.current_set">
        <div class="grid-2">
          <section class="card">
            <div class="card-head">
              <h2>점수 흐름</h2>
              <div class="tabs" role="group" aria-label="세트 선택">
                <button v-for="n in setNumbers" :key="n" type="button" class="tab" :aria-pressed="shownSet === n"
                        @click="picked = n === s.current_set ? null : n">{{ n }}세트</button>
              </div>
            </div>
            <ScoreFlow v-if="flowEvents.length" :events="flowEvents" :home="match.home.name" :away="match.away.name" />
            <p v-else class="empty small">이 세트는 아직 득점이 없습니다.</p>
          </section>

          <section class="card">
            <div class="card-head">
              <h2>{{ metricsView === 'set' ? `${shownSet}세트 지표` : '경기 전체 지표' }}</h2>
              <div class="tabs" role="group" aria-label="지표 범위">
                <button type="button" class="tab" :aria-pressed="metricsView === 'set'" @click="metricsView = 'set'">세트</button>
                <button type="button" class="tab" :aria-pressed="metricsView === 'match'" @click="metricsView = 'match'">전체</button>
              </div>
            </div>
            <CompareBars :rows="metricRows" :a-label="match.home.name" :b-label="match.away.name" />
            <p class="muted small foot">문자중계로 바로 센 값입니다. 경기 후 공식 기록과 다를 수 있습니다.</p>
          </section>
        </div>

        <section class="card">
          <div class="card-head"><h2>최근 득점</h2><span class="muted small">새 득점이 위에 표시됩니다</span></div>
          <ol v-if="recent.length" class="feed">
            <li v-for="p in recent" :key="`${p.set}-${p.point_no}`" :class="sideOf(p.winner)">
              <span class="score">{{ p.home_score }}:{{ p.away_score }}</span>
              <span class="set muted small">{{ p.set }}세트</span>
              <span class="who">{{ name(p.winner) }}</span>
              <span class="what">{{ describe(p) }}</span>
            </li>
          </ol>
          <p v-else class="empty small">아직 득점이 없습니다.</p>
        </section>
      </template>

      <p class="muted small source">
        자료: KOVO 문자중계 (<a href="https://www.kovo.co.kr" target="_blank" rel="noopener noreferrer">kovo.co.kr</a>) ·
        수집 후 화면 반영까지 30초 이내를 목표로 합니다.
      </p>
    </template>
  </AsyncState>
</template>

<style scoped>
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.top { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.mode { display: inline-flex; align-items: center; gap: 6px; padding: 1px 10px; border-radius: 999px; font-weight: 700; font-size: .78rem; }
.mode.live { background: var(--color-accent-soft); color: var(--color-accent); }
.mode.replay { background: var(--color-muted); color: var(--color-muted-foreground); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
.conn { color: var(--color-muted-foreground); font-size: .78rem; font-variant-numeric: tabular-nums; }
.conn.open::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--color-good);
                     margin-right: 5px; vertical-align: 1px; }
.conn.reconnecting, .conn.closed { color: var(--color-bad); font-weight: 600; }
.note { margin: var(--space-3) 0 0; padding: 8px 12px; background: var(--color-muted); border-radius: 8px; }

.board { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: var(--space-4); margin: var(--space-4) 0 var(--space-2); }
.team { display: flex; flex-direction: column; align-items: center; gap: 2px; color: var(--color-foreground); text-align: center; min-width: 0; }
.team:hover { text-decoration: none; }
.team .side { color: var(--color-muted-foreground); }
.tname { font-size: 1.35rem; font-weight: 800; overflow-wrap: anywhere; }
.team.home .tname { color: var(--color-home); }
.team.away .tname { color: var(--color-away); }
.serve { display: inline-flex; align-items: center; gap: 4px; min-height: 20px; font-weight: 700; color: var(--color-foreground); }
.serve svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.2; visibility: hidden; }
.serve.on svg { visibility: visible; }
.center { text-align: center; font-variant-numeric: tabular-nums; }
.sets { display: flex; justify-content: center; gap: 8px; font-size: 1.5rem; font-weight: 800; color: var(--color-muted-foreground); }
.set-label { color: var(--color-muted-foreground); margin: 2px 0; }
.points { display: flex; justify-content: center; align-items: baseline; gap: 10px; font-size: 3.2rem; line-height: 1.05; font-weight: 800; }
.points span, .sets span { color: var(--color-muted-foreground); font-weight: 500; }
.h { color: var(--color-home); }
.a { color: var(--color-away); }
.under { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.run { font-weight: 700; color: var(--color-heading); background: var(--color-primary-soft); padding: 1px 10px; border-radius: 999px; }
.set-scores { list-style: none; display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; margin: 0; padding: 0;
              font-variant-numeric: tabular-nums; }
.set-scores b { font-weight: 500; }
.set-scores b.w { font-weight: 800; }
.prob { margin-top: var(--space-4); }
.prob p { margin: 6px 0 0; }
.done { margin: var(--space-3) 0 0; text-align: center; }
.waiting { text-align: center; padding: var(--space-5) 0 var(--space-3); }
.waiting p { margin: 4px 0; }

.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.foot { margin: var(--space-3) 0 0; }
.feed { list-style: none; margin: 0; padding: 0; }
.feed li { display: grid; grid-template-columns: 56px 44px minmax(84px, auto) 1fr; gap: 10px; align-items: baseline;
           padding: 7px 4px; border-bottom: 1px solid var(--color-border-soft); }
.feed .score { font-weight: 800; font-variant-numeric: tabular-nums; }
.feed .home .score, .feed .home .who { color: var(--color-home); }
.feed .away .score, .feed .away .who { color: var(--color-away); }
.feed .who { font-weight: 700; }
.source { margin-top: var(--space-4); text-align: center; }
@media (max-width: 560px) {
  .tname { font-size: 1.05rem; }
  .points { font-size: 2.4rem; }
  .feed li { grid-template-columns: 48px 1fr auto; grid-template-areas: "score who set" "score what what"; row-gap: 0; }
  .feed .score { grid-area: score; }
  .feed .who { grid-area: who; }
  .feed .set { grid-area: set; }
  .feed .what { grid-area: what; }
}
</style>
