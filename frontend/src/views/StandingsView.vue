<script setup>
// WEB-05 순위 화면: 팀 순위표(포스트시즌 진출 구간 표시)와 부문별 선수 순위.
// 모션: 선수 순위 부문을 바꾸면 목록에 남는 선수는 새 순위 자리로 미끄러지고, 새로 들어온 이름은 서서히 나타난다.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getPlayerRanking, getStandings } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FilterBar from '@/components/FilterBar.vue'
import { useFiltersStore } from '@/stores/filters'
import { num, postseasonZone, ratio } from '@/utils/format'

// 선수 순위 부문(API의 category 값 → 탭 이름)
const CATEGORIES = { points: '득점', attack: '공격 성공률', serve: '서브', block: '블로킹', receive: '리시브 효율', dig: '디그', set: '세트' }
const filters = useFiltersStore()
const route = useRoute()
const standings = ref(null)
const ranking = ref(null)
const category = ref('points')
const state = ref({ loading: true, error: null })
const rankState = ref({ loading: true, error: null })

// 팀 순위표 불러오기
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
// 선수 순위(지금 고른 부문) 불러오기
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
// 팀 코드 → 'po'(포스트시즌 진출) | 'semi'(준플레이오프) 표시
const zone = computed(() => postseasonZone(standings.value?.rows))
// 부문 단위에 맞춘 값 표시(% / 점 / 세트당 소수 셋째 자리)
const fmtValue = (r) => (ranking.value.unit === '%' ? `${num(r.value, 2)}%` : ranking.value.unit === '점' ? num(r.value) : num(r.value, 3))

onMounted(async () => {
  await filters.init()
  const g = route.query.gender
  if (['M', 'F'].includes(g)) filters.set({ gender: g })
  else if (!['M', 'F'].includes(filters.gender)) filters.set({ gender: 'M' }) // 순위는 남녀부 중 하나를 골라야 한다
  await Promise.all([loadStandings(), loadRanking()])
})
// 시즌·남녀부가 바뀌면 둘 다, 부문이 바뀌면 선수 순위만 다시 불러온다
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
    <AsyncState :loading="state.loading && !standings" :error="state.error" :empty="!standings?.rows.length">
      <div class="table-wrap" :class="{ busy: state.loading }" :aria-busy="state.loading">
        <table class="data">
          <thead>
            <tr><th>순위</th><th class="left">팀</th><th>경기</th><th>승</th><th>패</th><th>승점</th>
              <th>세트 득실</th><th>세트 득실률</th><th>점수 득실률</th><th class="left">최근 5경기</th></tr>
          </thead>
          <TransitionGroup tag="tbody" name="row">
            <tr v-for="r in standings.rows" :key="r.team.code" :class="zone[r.team.code]">
              <td class="rank">{{ r.rank }}<span v-if="zone[r.team.code]" class="sr-only">{{ zone[r.team.code] === 'po' ? ', 포스트시즌권' : ', 준플레이오프권' }}</span></td>
              <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
              <td>{{ r.games }}</td><td>{{ r.wins }}</td><td>{{ r.losses }}</td><td class="strong">{{ r.points }}</td>
              <td>{{ r.sets_won }}:{{ r.sets_lost }}</td><td>{{ r.games ? ratio(r.set_ratio) : '-' }}</td><td>{{ r.games ? ratio(r.point_ratio) : '-' }}</td>
              <td class="left"><span class="form"><span v-for="(f, i) in r.form" :key="i" class="badge" :class="f.toLowerCase()">{{ f === 'W' ? '승' : '패' }}</span></span></td>
            </tr>
          </TransitionGroup>
        </table>
      </div>
      <p class="zone-legend small muted"><span class="key po" /> 포스트시즌권(1~3위) <span class="key semi" /> 준플레이오프권(4위가 3위와 승점 3점 이내)</p>
      <p class="small muted">순위 기준: {{ standings.rule }}. 승자승은 반영하지 않았습니다.</p>
    </AsyncState>
  </section>

  <section class="card">
    <div class="card-head"><h2>선수 순위</h2>
      <RouterLink :to="{ path: '/ratings', query: { gender: filters.gender } }" class="small">포지션별 능력치 순위 →</RouterLink></div>
    <div v-glider class="tabs" role="group" aria-label="선수 순위 부문">
      <button v-for="(label, c) in CATEGORIES" :key="c" type="button" class="tab" :aria-pressed="category === c" @click="category = c">{{ label }}</button>
    </div>
    <AsyncState :loading="rankState.loading && !ranking" :error="rankState.error" :empty="!ranking?.rows.length"
                empty-text="순위에 들 만큼 출전한 선수가 아직 없습니다.">
      <div class="table-wrap" :class="{ busy: rankState.loading }" :aria-busy="rankState.loading">
        <table class="data">
          <thead>
            <tr><th>순위</th><th class="left">선수</th><th class="left">팀</th><th>경기</th><th>출전 세트</th>
              <th>{{ ranking.label }}</th>
              <th v-if="category === 'attack'">성공/시도</th><th v-if="category === 'receive'">시도</th></tr>
          </thead>
          <TransitionGroup tag="tbody" name="row">
            <tr v-for="r in ranking.rows" :key="r.player.code">
              <td>{{ r.rank }}</td>
              <td class="left"><RouterLink :to="`/players/${r.player.code}`">{{ r.player.name }}</RouterLink></td>
              <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
              <td>{{ r.games }}</td><td>{{ r.sets_played }}</td><td class="strong">{{ fmtValue(r) }}</td>
              <td v-if="category === 'attack'">{{ r.attack_success }}/{{ r.attack_attempts }}</td>
              <td v-if="category === 'receive'">{{ r.receive_attempts }}</td>
            </tr>
          </TransitionGroup>
        </table>
      </div>
      <p class="small muted">순위 자격: {{ ranking.qualification }}</p>
    </AsyncState>
  </section>
</template>

<style scoped>
.form { display: inline-flex; gap: 3px; }
.busy { opacity: .55; transition: opacity var(--dur-fast) linear; }
</style>
