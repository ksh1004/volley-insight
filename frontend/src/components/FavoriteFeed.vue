<script setup>
// WEB-12: the viewer's favourite teams (record, next and last match) and players (last game, season so far),
// shown first on the home page. Hidden for signed-out viewers.
import { computed, onMounted, ref } from 'vue'
import { getFeed } from '@/api/client'
import MatchCard from '@/components/MatchCard.vue'
import { GENDERS, POSITIONS } from '@/constants/volley'
import { useAuthStore } from '@/stores/auth'
import { fmtDate, num, pct } from '@/utils/format'

const auth = useAuthStore()
const feed = ref(null)
const error = ref('')
const hasAny = computed(() => auth.favorites.teams.length + auth.favorites.players.length > 0)

onMounted(async () => {
  await auth.init()
  if (!auth.isLoggedIn || !hasAny.value) return
  try {
    feed.value = await getFeed()
  } catch (e) {
    error.value = e.message
  }
})

// the team's last match from its own side
function lastResult(t) {
  const m = t.last
  const home = m.home.code === t.team.code
  const [mine, theirs] = home ? [m.home_sets, m.away_sets] : [m.away_sets, m.home_sets]
  return { opponent: home ? m.away.name : m.home.name, score: `${mine}:${theirs}`, won: mine > theirs }
}
const perSet = (s) => (s?.sets_played ? num(s.points / s.sets_played, 2) : '-')
const FORM = { up: ['상승세', 'w'], down: ['하락세', 'down'], mixed: ['엇갈림', 'l'] }
</script>

<template>
  <section v-if="auth.isLoggedIn" class="card" aria-labelledby="fav-title">
    <div class="card-head">
      <h2 id="fav-title">내 관심 <span v-if="feed" class="muted small">{{ feed.season.years.replace('-', '–') }} 시즌</span></h2>
      <RouterLink to="/me" class="small">관심 목록 관리 →</RouterLink>
    </div>

    <p v-if="!hasAny" class="hint">
      관심 팀이나 선수를 정하면 여기에 순위, 다음 경기, 최근 기록을 먼저 보여 드립니다.
      <RouterLink to="/me">관심 팀 고르기</RouterLink> 또는 선수 화면의 <b>관심 선수</b> 버튼을 누르세요.
    </p>
    <p v-else-if="error" class="muted small">관심 팀 정보를 불러오지 못했습니다. ({{ error }})</p>
    <div v-else-if="!feed" class="skeleton" aria-busy="true" aria-label="불러오는 중" />

    <template v-else>
      <div v-if="feed.teams.length" class="teams">
        <article v-for="t in feed.teams" :key="t.team.code" class="team">
          <header>
            <RouterLink :to="`/teams/${t.team.code}`" class="name">{{ t.team.name }}</RouterLink>
            <span class="muted small">{{ GENDERS[t.team.gender] }}</span>
          </header>
          <p v-if="t.record" class="record small">
            <span><b>{{ t.record.rank }}위</b> · {{ t.record.wins }}승 {{ t.record.losses }}패 · 승점 {{ t.record.points }}</span>
            <span class="form" :aria-label="`최근 ${t.record.form.length}경기 ${t.record.form.map((f) => (f === 'W' ? '승' : '패')).join(' ')}`">
              <span v-for="(f, i) in t.record.form" :key="i" class="badge" :class="f.toLowerCase()" aria-hidden="true">{{ f === 'W' ? '승' : '패' }}</span>
            </span>
          </p>
          <p v-else class="record small muted">이번 시즌 정규리그 기록이 아직 없습니다.</p>
          <MatchCard v-if="t.next" :match="t.next" show-date />
          <p v-else class="muted small">예정된 경기가 없습니다.</p>
          <p v-if="t.last" class="last small">
            지난 경기 <RouterLink :to="`/matches/${t.last.key}`">{{ fmtDate(t.last.scheduled_at) }} {{ lastResult(t).opponent }}전
              {{ lastResult(t).score }}</RouterLink>
            <span class="badge" :class="lastResult(t).won ? 'w' : 'l'">{{ lastResult(t).won ? '승' : '패' }}</span>
          </p>
        </article>
      </div>

      <ul v-if="feed.players.length" class="players" :class="{ sep: feed.teams.length }">
        <li v-for="p in feed.players" :key="p.player.code">
          <div class="who">
            <RouterLink :to="`/players/${p.player.code}`" class="name">{{ p.player.name }}</RouterLink>
            <span class="muted small">{{ p.team?.name }}<template v-if="POSITIONS[p.player.position]"> · {{ POSITIONS[p.player.position] }}</template></span>
            <span v-if="FORM[p.form]" class="badge" :class="FORM[p.form][1]">{{ FORM[p.form][0] }}</span>
          </div>
          <template v-if="p.last">
            <p class="line small">
              <span class="muted">최근</span>
              <RouterLink :to="`/matches/${p.last.match}`">{{ fmtDate(p.last.date) }} {{ p.last.opponent }}전 {{ p.last.score }}</RouterLink>
              · <b>{{ p.last.points }}점</b> ({{ p.last.sets }}세트)
              <template v-if="p.last.attack_pct != null"> · 공격 {{ pct(p.last.attack_pct) }}</template>
              <template v-if="p.last.receive_efficiency != null"> · 리시브 {{ pct(p.last.receive_efficiency) }}</template>
            </p>
            <p class="line small">
              <span class="muted">시즌</span> {{ p.season.games }}경기 · 세트당 {{ perSet(p.season) }}점
              <template v-if="p.season.attack_pct != null"> · 공격 {{ pct(p.season.attack_pct) }}</template>
              <template v-if="p.season.receive_efficiency != null"> · 리시브 {{ pct(p.season.receive_efficiency) }}</template>
            </p>
          </template>
          <p v-else class="line small muted">이번 시즌 출전 기록이 없습니다.</p>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.hint { margin: 0; color: var(--color-muted-foreground); }
.skeleton { height: 120px; border-radius: 8px; background: var(--color-muted); }
.teams { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.team { display: grid; gap: 8px; align-content: start; padding: 12px; border: 1px solid var(--color-border-strong); border-radius: var(--radius);
        background: var(--color-primary-soft); }
.team header { display: flex; align-items: baseline; gap: 8px; }
.name { font-weight: 700; font-size: 1.02rem; }
.record { display: flex; align-items: center; gap: 4px 8px; flex-wrap: wrap; margin: 0; font-variant-numeric: tabular-nums; }
.record b { color: var(--color-heading); }
.form { display: inline-flex; gap: 3px; }
.last { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 0; }
.players { list-style: none; margin: 0; padding: 0; }
.players.sep { margin-top: var(--space-3); }
.players li { padding: 8px 0; border-bottom: 1px solid var(--color-border-soft); }
.players li:last-child { border-bottom: 0; }
.who { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.line { margin: 3px 0 0; font-variant-numeric: tabular-nums; }
.line .muted { display: inline-block; min-width: 2.4em; }
.badge.down { background: #fef2f2; color: var(--color-bad); }
</style>
