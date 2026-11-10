<script setup>
// 팀이나 선수 태그가 붙은 최신 기사 5개(팀·선수 화면 아래쪽). 선수 화면은 기사가 없으면 카드를 아예 숨긴다
// (hide-empty: 대부분의 선수는 기사 제목에 잘 나오지 않으므로 빈 카드가 많아지는 것을 막음).
import { onMounted, ref } from 'vue'
import { getArticles } from '@/api/client'
import ArticleList from '@/components/ArticleList.vue'
import AsyncState from '@/components/AsyncState.vue'

const props = defineProps({ team: String, player: String, hideEmpty: Boolean })
const items = ref([])
const total = ref(0)
const state = ref({ loading: true, error: null })

onMounted(async () => {
  try {
    const r = await getArticles({ team: props.team, player: props.player, page_size: 5 })
    items.value = r.results
    total.value = r.count
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
  }
})
</script>

<template>
  <section v-if="!(hideEmpty && !state.loading && !items.length)" class="card">
    <div class="card-head">
      <h2>관련 기사 <span v-if="total" class="muted small">{{ total }}건</span></h2>
      <RouterLink v-if="team && total > items.length" :to="{ path: '/news', query: { team } }" class="small">더 보기 →</RouterLink>
    </div>
    <AsyncState :loading="state.loading" :error="state.error" :empty="!items.length" :rows="3"
                empty-text="최근 기사에서 이 팀을 찾지 못했습니다.">
      <ArticleList :articles="items" compact />
    </AsyncState>
  </section>
</template>
