<script setup>
// WEB-10: one search over teams, players (Elasticsearch `entities`) and articles (`articles`).
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { searchAll } from '@/api/client'
import ArticleList from '@/components/ArticleList.vue'
import AsyncState from '@/components/AsyncState.vue'
import { GENDERS, POSITIONS } from '@/constants/volley'

const route = useRoute()
const router = useRouter()
const q = computed(() => String(route.query.q || '').trim())
const text = ref(q.value)
const data = ref(null)
const articles = ref([])
const page = ref(1)
const state = ref({ loading: false, more: false, error: null })
const EXAMPLES = ['대한항공', '김연경', '현대건설', '프로배구 개막']

async function load(reset = true) {
  if (!q.value) { data.value = null; return }
  if (reset) { page.value = 1; state.value = { loading: true, more: false, error: null } } else state.value.more = true
  try {
    const r = await searchAll(q.value, page.value)
    if (reset) data.value = r
    articles.value = reset ? r.articles.results : [...articles.value, ...r.articles.results]
  } catch (e) {
    state.value.error = e.response?.data?.detail || e.message
  } finally {
    state.value.loading = false
    state.value.more = false
  }
}

watch(q, (v) => { text.value = v; load() }, { immediate: true })
const submit = () => text.value.trim() && router.push({ query: { q: text.value.trim() } })
const more = () => { page.value += 1; load(false) }
const nothing = computed(() => data.value && !data.value.teams.length && !data.value.players.length && !data.value.articles.total)
const hasMore = computed(() => data.value && articles.value.length < data.value.articles.total)
</script>

<template>
  <h1>검색</h1>
  <form class="card search-form" role="search" @submit.prevent="submit">
    <label for="search-page-input" class="sr-only">검색어</label>
    <input id="search-page-input" v-model="text" type="search" placeholder="팀, 선수 이름이나 기사 내용" />
    <button type="submit" class="tab active">검색</button>
  </form>

  <section v-if="!q" class="card">
    <p class="muted">팀·선수 이름이나 기사 낱말로 찾을 수 있어요. 예:
      <RouterLink v-for="e in EXAMPLES" :key="e" :to="{ query: { q: e } }" class="example">{{ e }}</RouterLink>
    </p>
  </section>

  <AsyncState v-else :loading="state.loading" :error="state.error">
    <section v-if="nothing" class="card" aria-live="polite">
      <p><strong>'{{ q }}'</strong>에 맞는 팀, 선수, 기사가 없습니다.</p>
      <p class="muted small">더 짧게 입력해 보세요(예: '김연경' 대신 '연경'). 다른 검색어:
        <RouterLink v-for="e in EXAMPLES" :key="e" :to="{ query: { q: e } }" class="example">{{ e }}</RouterLink>
      </p>
    </section>
    <template v-else-if="data">
      <div class="grid-2" v-if="data.teams.length || data.players.length">
        <section class="card">
          <h2>팀 <span class="muted small">{{ data.teams.length }}</span></h2>
          <ul class="entities" v-if="data.teams.length">
            <li v-for="t in data.teams" :key="t.code">
              <RouterLink :to="`/teams/${t.code}`">{{ t.name }}</RouterLink>
              <span class="muted small">{{ GENDERS[t.gender] }}{{ t.active ? '' : ' · 지난 시즌 팀' }}</span>
            </li>
          </ul>
          <p v-else class="muted small">맞는 팀이 없습니다.</p>
        </section>
        <section class="card">
          <h2>선수 <span class="muted small">{{ data.players.length }}</span></h2>
          <ul class="entities" v-if="data.players.length">
            <li v-for="p in data.players" :key="p.code">
              <RouterLink :to="`/players/${p.code}`">{{ p.name }}</RouterLink>
              <span class="muted small">{{ [p.team_name, POSITIONS[p.position]].filter(Boolean).join(' · ') }}{{ p.active ? '' : ' · 최근 1년 출전 없음' }}</span>
            </li>
          </ul>
          <p v-else class="muted small">맞는 선수가 없습니다.</p>
        </section>
      </div>
      <section class="card">
        <h2>기사 <span class="muted small">{{ data.articles.total.toLocaleString('ko-KR') }}건 · 관련도순</span></h2>
        <ArticleList v-if="articles.length" :articles="articles" highlight />
        <p v-else class="muted small">맞는 기사가 없습니다.</p>
        <button v-if="hasMore" type="button" class="tab more" :disabled="state.more" @click="more">
          {{ state.more ? '불러오는 중…' : '더 보기' }}
        </button>
      </section>
    </template>
  </AsyncState>
</template>

<style scoped>
.search-form { display: flex; gap: var(--space-2); }
.search-form input { flex: 1; font: inherit; padding: 8px 12px; min-height: 40px; border: 1px solid var(--color-border); border-radius: 8px;
                     background: var(--color-card); color: var(--color-foreground); }
.search-form input:focus { border-color: var(--color-primary); }
.example { margin-left: 8px; }
.entities { list-style: none; margin: 0; padding: 0; }
.entities li { display: flex; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--color-border-soft); }
.entities li:last-child { border-bottom: 0; }
.more { display: block; margin: var(--space-3) auto 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (pointer: coarse) { .search-form input { min-height: 44px; } }
</style>
