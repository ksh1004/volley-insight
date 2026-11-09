<script setup>
// WEB-07: profile, regular-season totals by season, match-by-match log of one season.
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getPlayer } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FavoriteButton from '@/components/FavoriteButton.vue'
import PercentileBars from '@/components/PercentileBars.vue'
import PlayerForm from '@/components/PlayerForm.vue'
import PlayerRatingCard from '@/components/PlayerRatingCard.vue'
import PlayerStyle from '@/components/PlayerStyle.vue'
import RelatedArticles from '@/components/RelatedArticles.vue'
import SortableTable from '@/components/SortableTable.vue'
import { GENDERS, POSITIONS } from '@/constants/volley'
import { fmtDate, num, pct } from '@/utils/format'

const LOG_COLS = [
  { key: 'date', label: '날짜', left: true, sticky: true, get: (m) => m.date },
  { key: 'opponent', label: '상대', left: true, get: (m) => m.opponent.name },
  { key: 'result', label: '결과', get: (m) => m.result },
  { key: 'sets_played', label: '출전 세트' },
  { key: 'points', label: '득점', strong: true },
  { key: 'attack', label: '공격 성공/시도', get: (m) => (m.attack_attempts ? m.attack_success / m.attack_attempts : null),
    fmt: (m) => `${m.attack_success}/${m.attack_attempts}` },
  { key: 'block_success', label: '블로킹' },
  { key: 'serve_aces', label: '서브 에이스' },
  { key: 'receive', label: '리시브 정확/시도', get: (m) => m.receive_attempts || null, fmt: (m) => `${m.receive_excellent}/${m.receive_attempts}` },
  { key: 'dig_success', label: '디그' },
  { key: 'set_success', label: '세트' },
  { key: 'errors', label: '범실' },
]
const props = defineProps({ code: { type: String, required: true } })
const route = useRoute()
const data = ref(null)
const season = ref(route.query.season || '')
// season code -> "2025–2026" from the season names in the career rows (falls back to the code)
const seasonYears = (code) => {
  const m = data.value?.career.find((r) => r.season.code === code)?.season.name.match(/(\d{4})~(\d{4})/)
  return m ? `${m[1]}–${m[2]}` : code
}
const state = ref({ loading: true, error: null })

async function load() {
  state.value = { loading: true, error: null }
  try {
    data.value = await getPlayer(props.code, season.value)
    season.value = data.value.season.code
  } catch (e) {
    state.value.error = e.response?.status === 404 ? '선수를 찾을 수 없습니다.' : e.message
  } finally {
    state.value.loading = false
  }
}
onMounted(load)
watch(season, (v) => { if (data.value && v !== data.value.season.code) load() })
</script>

<template>
  <AsyncState :loading="state.loading && !data" :error="state.error">
    <template v-if="data">
      <div class="title-row">
        <div>
          <h1>{{ data.player.name }} <span class="muted num" v-if="data.back_number">{{ data.back_number }}</span></h1>
          <p class="muted small">
            <RouterLink v-if="data.team" :to="`/teams/${data.team.code}`">{{ data.team.name }}</RouterLink>
            · {{ POSITIONS[data.player.position] || '포지션 미상' }} · {{ GENDERS[data.player.gender] }}
            <span v-if="data.player.height_cm"> · {{ data.player.height_cm }}cm</span>
          </p>
        </div>
        <FavoriteButton kind="player" :code="data.player.code" :name="data.player.name" class="fav-btn" />
      </div>

      <section v-if="data.ratings" class="card">
        <h2>능력치 카드 <span class="muted small">정규리그 · 같은 성별·포지션 비교</span></h2>
        <PlayerRatingCard :ratings="data.ratings" :season="data.season.code" :gender="data.player.gender" />
      </section>

      <div :class="{ 'grid-2': data.profile && data.form }">
        <section v-reveal class="card" v-if="data.profile">
          <h2>포지션 내 위치 <span class="muted small">{{ data.season.years }} 정규리그</span></h2>
          <PercentileBars :profile="data.profile" />
        </section>
        <section v-reveal class="card" v-if="data.form">
          <h2>최근 폼 <span class="muted small">{{ data.season.years }} 시즌</span></h2>
          <PlayerForm :form="data.form" />
        </section>
      </div>

      <section v-reveal class="card" v-if="data.style">
        <h2>선수 유형과 비슷한 선수 <span class="muted small">{{ data.season.years }} 정규리그</span></h2>
        <PlayerStyle :style="data.style" :season="data.season.code" />
      </section>

      <section v-reveal class="card">
        <h2>시즌별 기록 <span class="muted small">정규리그</span></h2>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th class="left sticky">시즌</th><th class="left">팀</th><th>경기</th><th>세트</th><th>득점</th>
              <th>공격 성공률</th><th>공격 효율</th><th>세트당 블로킹</th><th>세트당 서브</th><th>리시브 효율</th><th>세트당 디그</th><th>세트당 세트</th></tr></thead>
            <tbody>
              <tr v-for="c in data.career" :key="c.season.code + c.team.code">
                <td class="left sticky">{{ c.season.name.match(/\d{4}~\d{4}/)?.[0] || c.season.code }}</td>
                <td class="left"><RouterLink :to="`/teams/${c.team.code}`">{{ c.team.name }}</RouterLink></td>
                <td>{{ c.games }}</td><td>{{ c.sets_played }}</td><td class="strong">{{ c.points }}</td>
                <td>{{ pct(c.attack_pct) }}</td><td>{{ pct(c.attack_efficiency) }}</td>
                <td>{{ num(c.blocks_per_set, 2) }}</td><td>{{ num(c.serve_aces_per_set, 2) }}</td>
                <td>{{ pct(c.receive_efficiency) }}</td><td>{{ num(c.digs_per_set, 2) }}</td><td>{{ num(c.sets_per_set, 2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!data.career.length" class="empty">정규리그 기록이 없습니다.</p>
      </section>

      <section v-reveal class="card">
        <div class="card-head">
          <h2>경기별 기록</h2>
          <select v-model="season" aria-label="시즌">
            <option v-for="s in data.seasons" :key="s" :value="s">{{ seasonYears(s) }} 시즌</option>
          </select>
        </div>
        <AsyncState :loading="state.loading" :error="null" :empty="!data.matches.length" empty-text="이 시즌 출전 기록이 없습니다.">
          <SortableTable :columns="LOG_COLS" :rows="data.matches" :row-key="(m) => m.match"
                         :initial-sort="{ key: 'date', dir: 'asc' }" caption="경기별 기록">
            <template #cell-date="{ row }"><RouterLink :to="`/matches/${row.match}`">{{ fmtDate(row.date) }}</RouterLink></template>
            <template #cell-opponent="{ row }">
              <span class="muted">{{ row.home ? 'vs' : '@' }}</span> <RouterLink :to="`/teams/${row.opponent.code}`">{{ row.opponent.name }}</RouterLink>
            </template>
            <template #cell-result="{ row }">
              <span v-if="row.result" class="badge" :class="row.result.toLowerCase()">{{ row.result === 'W' ? '승' : '패' }} {{ row.score }}</span>
            </template>
          </SortableTable>
        </AsyncState>
      </section>

      <RelatedArticles :player="code" hide-empty />
    </template>
  </AsyncState>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.title-row h1 { margin-bottom: 4px; }
.fav-btn { margin-bottom: 12px; }
.title-row p { margin: 0 0 16px; }
.num { font-weight: 600; }
</style>
