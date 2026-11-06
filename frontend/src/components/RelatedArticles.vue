<script setup>
// Latest articles tagged with a team or a player (team/player pages). Player cards hide when there are none,
// since most players are rarely named in headlines.
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
