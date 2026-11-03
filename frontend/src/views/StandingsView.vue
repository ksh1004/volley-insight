<script setup>
// WEB-05: team standings and player rankings by category.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getPlayerRanking, getStandings } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FilterBar from '@/components/FilterBar.vue'
import { useFiltersStore } from '@/stores/filters'
import { num, postseasonZone, ratio } from '@/utils/format'

const CATEGORIES = { points: '득점', attack: '공격 성공률', serve: '서브', block: '블로킹', receive: '리시브 효율', dig: '디그', set: '세트' }
const filters = useFiltersStore()
const route = useRoute()
const standings = ref(null)
const ranking = ref(null)
const category = ref('points')
const state = ref({ loading: true, error: null })
const rankState = ref({ loading: true, error: null })

async function loadStandings() {
  state.value = { loading: true, error: null }
  try {
    standings.value = await getStandings({ season: filters.season, gender: filters.gender })
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
  }
}
async function loadRanking() {
  rankState.value = { loading: true, error: null }
  try {
    ranking.value = await getPlayerRanking({ season: filters.season, gender: filters.gender, category: category.value })
  } catch (e) {
    rankState.value.error = e.message
  } finally {
    rankState.value.loading = false
  }
}
const zone = computed(() => postseasonZone(standings.value?.rows))
const fmtValue = (r) => (ranking.value.unit === '%' ? `${num(r.value, 2)}%` : ranking.value.unit === '점' ? num(r.value) : num(r.value, 3))

onMounted(async () => {
  await filters.init()
  const g = route.query.gender
  if (['M', 'F'].includes(g)) filters.set({ gender: g })
  else if (!['M', 'F'].includes(filters.gender)) filters.set({ gender: 'M' }) // standings need a division
  await Promise.all([loadStandings(), loadRanking()])
})
watch(() => [filters.season, filters.gender], () => Promise.all([loadStandings(), loadRanking()]))
watch(category, loadRanking)
</script>

<template>
  <h1>순위</h1>
  <FilterBar />
  <section class="card">
    <div class="card-head">
      <h2>팀 순위 <span class="muted small">정규리그</span></h2>
    </div>
    <AsyncState :loading="state.loading" :error="state.error" :empty="!standings?.rows.length">
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr><th>순위</th><th class="left">팀</th><th>경기</th><th>승</th><th>패</th><th>승점</th>
              <th>세트 득실</th><th>세트 득실률</th><th>점수 득실률</th><th class="left">최근 5경기</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in standings.rows" :key="r.team.code" :class="zone[r.team.code]">
              <td class="rank">{{ r.rank }}<span v-if="zone[r.team.code]" class="sr-only">{{ zone[r.team.code] === 'po' ? ', 포스트시즌권' : ', 준플레이오프권' }}</span></td>
              <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
              <td>{{ r.games }}</td><td>{{ r.wins }}</td><td>{{ r.losses }}</td><td class="strong">{{ r.points }}</td>
              <td>{{ r.sets_won }}:{{ r.sets_lost }}</td><td>{{ r.games ? ratio(r.set_ratio) : '-' }}</td><td>{{ r.games ? ratio(r.point_ratio) : '-' }}</td>
              <td class="left"><span class="form"><span v-for="(f, i) in r.form" :key="i" class="badge" :class="f.toLowerCase()">{{ f === 'W' ? '승' : '패' }}</span></span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="legend small muted"><span class="key po" /> 포스트시즌권(1~3위) <span class="key semi" /> 준플레이오프권(4위가 3위와 승점 3점 이내)</p>
      <p class="small muted">순위 기준: {{ standings.rule }}. 승자승은 반영하지 않았습니다.</p>
    </AsyncState>
  </section>

  <section class="card">
    <div class="card-head"><h2>선수 순위</h2></div>
    <div class="tabs" role="group" aria-label="선수 순위 부문">
      <button v-for="(label, c) in CATEGORIES" :key="c" type="button" class="tab" :aria-pressed="category === c" @click="category = c">{{ label }}</button>
    </div>
    <AsyncState :loading="rankState.loading" :error="rankState.error" :empty="!ranking?.rows.length"
                empty-text="순위에 들 만큼 출전한 선수가 아직 없습니다.">
      <div class="table-wrap">
        <table class="data">
          <thead>
            <tr><th>순위</th><th class="left">선수</th><th class="left">팀</th><th>경기</th><th>출전 세트</th>
              <th>{{ ranking.label }}</th>
              <th v-if="category === 'attack'">성공/시도</th><th v-if="category === 'receive'">시도</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in ranking.rows" :key="r.player.code">
              <td>{{ r.rank }}</td>
              <td class="left"><RouterLink :to="`/players/${r.player.code}`">{{ r.player.name }}</RouterLink></td>
              <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
              <td>{{ r.games }}</td><td>{{ r.sets_played }}</td><td class="strong">{{ fmtValue(r) }}</td>
              <td v-if="category === 'attack'">{{ r.attack_success }}/{{ r.attack_attempts }}</td>
              <td v-if="category === 'receive'">{{ r.receive_attempts }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="small muted">순위 자격: {{ ranking.qualification }}</p>
    </AsyncState>
  </section>
</template>

<style scoped>
.form { display: inline-flex; gap: 3px; }
.rank { font-weight: 700; border-left: 3px solid transparent; }
tr.po .rank { border-left-color: var(--color-primary); }
tr.semi .rank { border-left-color: var(--color-accent); }
.legend { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 8px 0 0; }
.key { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.key.po { background: var(--color-primary); }
.key.semi { background: var(--color-accent); margin-left: 8px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
