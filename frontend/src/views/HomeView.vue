<script setup>
// WEB-01 홈 화면: 진행 중 경기, 오늘 경기(없으면 다가오는 경기), 최근 결과, 남녀부 순위, 최신 기사.
// WEB-12: 로그인한 사람에게는 관심 팀·선수 블록이 맨 위에 나오고, 관심 팀 경기가 목록 앞에 온다.
// 모션: 섹션과 카드가 화면에 들어올 때 떠오르며 나타난다(v-reveal, 격자 안에서는 순서대로 시차).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getArticles, getLive, getMatches, getStandings } from '@/api/client'
import ArticleList from '@/components/ArticleList.vue'
import AsyncState from '@/components/AsyncState.vue'
import FavoriteFeed from '@/components/FavoriteFeed.vue'
import LiveCard from '@/components/LiveCard.vue'
import MatchCard from '@/components/MatchCard.vue'
import { GENDERS } from '@/constants/volley'
import { useAuthStore } from '@/stores/auth'
import { useFiltersStore } from '@/stores/filters'
import { fmtFullDate, postseasonZone, todayKst } from '@/utils/format'

const filters = useFiltersStore()
const auth = useAuthStore()
const favTeams = computed(() => new Set(auth.favorites.teams.map((t) => t.code)))
const isFav = (m) => favTeams.value.has(m.home.code) || favTeams.value.has(m.away.code)
// 관심 팀 경기를 앞으로, 나머지는 서버가 준 순서(시각순) 그대로(sort는 같은 값끼리 순서를 유지)
const favFirst = (list) => [...list].sort((a, b) => isFav(b) - isFav(a))
const state = ref({ loading: true, error: null })
const today = ref([])
const upcoming = ref([])
const recent = ref([])
const standings = ref({})
const latestSeason = ref(null)
const articles = ref({ loading: true, items: [], error: null })

// 지금 실시간(또는 재생) 중인 경기들. 20초마다 새로 받아 오고, 카드는 실시간 화면으로 연결된다
const live = ref([])
let liveTimer = null
async function loadLive() {
  try {
    live.value = (await getLive()).results
  } catch {
    /* 실패해도 화면의 나머지는 이것과 상관없다 */
  }
}
onBeforeUnmount(() => clearInterval(liveTimer))

// 기사는 따로 불러온다: 기사 쪽이 느려도 경기 카드가 늦게 뜨지 않게
async function loadArticles() {
  try {
    articles.value.items = (await getArticles({ page_size: 6 })).results
  } catch (e) {
    articles.value.error = e.message
  } finally {
    articles.value.loading = false
  }
}

// 화면이 뜨면: 기사·실시간은 따로, 오늘 경기와 최근 결과는 함께, 그다음 순위표(남녀부 동시에)
onMounted(async () => {
  loadArticles()
  loadLive()
  liveTimer = setInterval(loadLive, 20000)
  try {
    await filters.init()
    latestSeason.value = filters.seasons[0]
    const [t, r] = await Promise.all([
      getMatches({ date: todayKst(), page_size: 20 }),
      getMatches({ status: 'finished', order: 'desc', page_size: 6 }),
    ])
    today.value = t.results
    recent.value = r.results
    if (!today.value.length) upcoming.value = (await getMatches({ status: 'scheduled', page_size: 4 })).results
    const [m, f] = await Promise.all(['M', 'F'].map((g) => getStandings({ season: latestSeason.value?.code, gender: g })))
    standings.value = { M: m, F: f }
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
  }
})
</script>

<template>
  <AsyncState :loading="state.loading" :error="state.error">
    <section v-if="live.length" v-reveal class="card" aria-labelledby="live-title">
      <div class="card-head">
        <h2 id="live-title">진행 중 경기</h2>
        <span class="muted small">20초마다 갱신 · 누르면 실시간 화면</span>
      </div>
      <div class="match-grid">
        <LiveCard v-for="(item, i) in live" :key="item.match.key" v-reveal="i" :item="item" />
      </div>
    </section>

    <FavoriteFeed />

    <section v-reveal class="card">
      <div class="card-head">
        <h2>{{ today.length ? '오늘 경기' : '다음 경기' }}</h2>
        <span class="muted small">{{ fmtFullDate(new Date().toISOString()) }}</span>
      </div>
      <div class="match-grid" v-if="today.length || upcoming.length">
        <MatchCard v-for="(m, i) in favFirst(today.length ? today : upcoming)" :key="m.key" v-reveal="i" :match="m" :show-date="!today.length"
                   :favorite="isFav(m)" />
      </div>
      <p v-else class="empty">예정된 경기가 없습니다.</p>
    </section>

    <section v-reveal class="card">
      <div class="card-head">
        <h2>최근 결과</h2>
        <RouterLink to="/schedule" class="small">전체 일정 →</RouterLink>
      </div>
      <div class="match-grid">
        <MatchCard v-for="(m, i) in recent" :key="m.key" v-reveal="i" :match="m" show-date :favorite="isFav(m)" />
      </div>
    </section>

    <div class="grid-2">
      <section v-for="(g, i) in ['M', 'F']" :key="g" v-reveal="i" class="card">
        <div class="card-head">
          <h2>{{ GENDERS[g] }} 순위</h2>
          <span class="muted small">{{ latestSeason?.years }} 정규리그</span>
        </div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>순위</th><th class="left">팀</th><th>경기</th><th>승</th><th>패</th><th>승점</th></tr></thead>
            <tbody>
              <tr v-for="r in standings[g]?.rows || []" :key="r.team.code"
                  :class="[postseasonZone(standings[g]?.rows)[r.team.code], { fav: favTeams.has(r.team.code) }]">
                <td class="rank">{{ r.rank }}</td>
                <td class="left"><RouterLink :to="`/teams/${r.team.code}`">{{ r.team.name }}</RouterLink></td>
                <td>{{ r.games }}</td><td>{{ r.wins }}</td><td>{{ r.losses }}</td><td class="strong">{{ r.points }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <RouterLink :to="{ path: '/standings', query: { gender: g } }" class="small more">순위 자세히 →</RouterLink>
      </section>
    </div>

    <section v-reveal class="card">
      <div class="card-head">
        <h2>최신 기사</h2>
        <RouterLink to="/news" class="small">기사 더 보기 →</RouterLink>
      </div>
      <AsyncState :loading="articles.loading" :error="articles.error" :empty="!articles.items.length"
                  empty-text="아직 수집된 기사가 없습니다." :rows="3">
        <ArticleList :articles="articles.items" />
      </AsyncState>
    </section>
  </AsyncState>
</template>

<style scoped>
.match-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.more { display: inline-block; margin-top: 8px; }
.rank { font-weight: 700; border-left: 3px solid transparent; }
tr.po .rank { border-left-color: var(--color-primary); }
tr.semi .rank { border-left-color: var(--color-accent); }
tr.fav td { background: var(--color-accent-soft); font-weight: 700; }
</style>
