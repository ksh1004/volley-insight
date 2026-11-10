<script setup>
// WEB-09 뉴스 화면: 최신 배구 기사, 팀으로 거르기. 필터는 주소(?team=)에 담아 링크로 공유할 수 있다.
// 20개씩 불러오고 '더 보기'로 이어 붙인다.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getArticles, getTeams } from '@/api/client'
import ArticleList from '@/components/ArticleList.vue'
import AsyncState from '@/components/AsyncState.vue'
import { GENDERS } from '@/constants/volley'

const route = useRoute()
const router = useRouter()
const team = computed(() => String(route.query.team || ''))
const teams = ref([])
const articles = ref([])
const total = ref(0)
const page = ref(1)
const hasMore = ref(false)
const state = ref({ loading: true, more: false, error: null })

// reset이면 첫 페이지부터 새로, 아니면 다음 페이지를 뒤에 붙인다('더 보기')
async function load(reset = true) {
  if (reset) { page.value = 1; state.value.loading = true } else state.value.more = true
  try {
    const r = await getArticles({ team: team.value, page: page.value, page_size: 20 })
    articles.value = reset ? r.results : [...articles.value, ...r.results]
    total.value = r.count
    hasMore.value = Boolean(r.next)
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
    state.value.more = false
  }
}

// '더 보기' 버튼
function more() { page.value += 1; load(false) }
// 팀 필터 선택: 주소만 바꾸면 위의 watch(team)가 다시 불러온다
function pick(code) { router.replace({ query: code ? { team: code } : {} }) }

onMounted(async () => {
  load()
  try { teams.value = await getTeams({}) } catch { teams.value = [] }
})
watch(team, () => load())

// 팀 필터 버튼을 남자부/여자부로 나눠 보여 주기 위한 묶음
const byGender = computed(() => ['M', 'F'].map((g) => ({ g, teams: teams.value.filter((t) => t.gender === g) })))
</script>

<template>
  <h1>배구 기사</h1>
  <section class="card">
    <div v-glider class="filters" role="group" aria-label="팀으로 거르기">
      <button type="button" class="tab" :aria-pressed="!team" @click="pick('')">전체</button>
      <template v-for="grp in byGender" :key="grp.g">
        <span class="group muted small">{{ GENDERS[grp.g] }}</span>
        <button v-for="t in grp.teams" :key="t.code" type="button" class="tab" :aria-pressed="team === t.code"
                @click="pick(t.code)">{{ t.name }}</button>
      </template>
    </div>
    <AsyncState :loading="state.loading" :error="state.error" :empty="!articles.length"
                empty-text="아직 수집된 기사가 없습니다. 기사는 매시 5분에 새로 모읍니다.">
      <p class="muted small count" aria-live="polite">{{ total.toLocaleString('ko-KR') }}건 · 최신순</p>
      <ArticleList :articles="articles" />
      <button v-if="hasMore" type="button" class="tab more" :disabled="state.more" @click="more">
        {{ state.more ? '불러오는 중…' : '더 보기' }}
      </button>
    </AsyncState>
    <p class="muted small source">기사 제목과 요약은 각 언론사가 제공한 정보입니다. 제목을 누르면 언론사 사이트에서 원문을 볼 수 있습니다.</p>
  </section>
</template>

<style scoped>
.filters { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: var(--space-3); }
.group { margin-left: var(--space-2); }
.count { margin: 0 0 4px; }
.more { display: block; margin: var(--space-3) auto 0; }
.source { margin: var(--space-4) 0 0; }
</style>
