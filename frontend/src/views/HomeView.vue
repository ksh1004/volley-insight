<script setup>
// WEB-01: today's games (or the next ones), latest results, standings of both divisions, latest articles.
// WEB-12: signed-in viewers see their favourite teams and players first, and their teams' games lead the lists.
// Motion: sections and cards rise in as they scroll into view (v-reveal, staggered within a grid).
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
// favourite teams' games first, otherwise the server order (time) is kept
const favFirst = (list) => [...list].sort((a, b) => isFav(b) - isFav(a))
const state = ref({ loading: true, error: null })
const today = ref([])
const upcoming = ref([])
const recent = ref([])
const standings = ref({})
const latestSeason = ref(null)
const articles = ref({ loading: true, items: [], error: null })

// games followed live (or replayed) right now; refreshed every 20 s, each card links to the live page
const live = ref([])
let liveTimer = null
async function loadLive() {
  try {
    live.value = (await getLive()).results
  } catch {
    /* the rest of the page does not depend on it */
  }
}
onBeforeUnmount(() => clearInterval(liveTimer))

// articles load on their own so a slow search side never holds up the match cards
async function loadArticles() {
  try {
    articles.value.items = (await getArticles({ page_size: 6 })).results
  } catch (e) {
    articles.value.error = e.message
  } finally {
    articles.value.loading = false
  }
}

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
