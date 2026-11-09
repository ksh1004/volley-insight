<script setup>
// Position ratings by position (player cards, spark_jobs/player_ratings.py): the top three as small cards, then every
// qualified player with the overall and each ability; any column sorts (rows slide to their new place).
// Old rows stay (dimmed) while another position or season loads, so switching never blanks the page.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getRatings } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import FilterBar from '@/components/FilterBar.vue'
import ScoreRing from '@/components/ScoreRing.vue'
import SortableTable from '@/components/SortableTable.vue'
import { ABILITY_HELP, POSITION_NAMES, POSITION_ORDER, bandOf } from '@/constants/ratings'
import { useFiltersStore } from '@/stores/filters'

const filters = useFiltersStore()
const route = useRoute()
const position = ref(POSITION_ORDER.includes(route.query.position) ? route.query.position : 'OH')
const includeAll = ref(false)
const data = ref(null)
const state = ref({ loading: true, error: null })

let requestNo = 0
async function load() {
  const mine = ++requestNo
  state.value = { loading: true, error: null }
  try {
    const res = await getRatings({ season: filters.season, gender: filters.gender, position: position.value,
                                   all: includeAll.value ? 1 : undefined })
    if (mine === requestNo) data.value = res
  } catch (e) {
    if (mine === requestNo) state.value.error = e.message
  } finally {
    if (mine === requestNo) state.value.loading = false
  }
}
onMounted(async () => {
  await filters.init()
  const g = route.query.gender
  if (['M', 'F'].includes(g)) filters.set({ gender: g })
  else if (!['M', 'F'].includes(filters.gender)) filters.set({ gender: 'M' }) // ratings are per division
  await load()
})
watch(() => [filters.season, filters.gender, position.value, includeAll.value], load)

// early in a season few players have 10+ sets: say so and offer the season before
const ranked = computed(() => (data.value?.rows || []).filter((r) => r.rank).length)
const previousSeason = computed(() => {
  const i = filters.seasons.findIndex((s) => s.code === data.value?.season.code)
  return i >= 0 ? filters.seasons[i + 1] : null
})
const early = computed(() => data.value && data.value.season.code === filters.seasons[0]?.code && ranked.value < 8)

const top3 = computed(() => (data.value?.rows || []).filter((r) => r.rank).slice(0, 3))
// the player's best counted ability, e.g. "블로킹 84" under his name on the podium
const strength = (r) => {
  const best = (data.value?.abilities || []).filter((a) => a.weight && r.scores[a.key] != null)
    .sort((a, b) => r.scores[b.key] - r.scores[a.key])[0]
  return best ? `${best.label} ${r.scores[best.key]}` : ''
}
const columns = computed(() => [
  { key: 'rank', label: '순위', get: (r) => r.rank ?? 999 , fmt: (r) => r.rank ?? '-' },
  { key: 'name', label: '선수', left: true, sticky: true, get: (r) => r.player.name },
  { key: 'team', label: '팀', left: true, get: (r) => r.team.name },
  { key: 'overall', label: '종합', strong: true, get: (r) => r.overall },
  ...(data.value?.abilities || []).map((a) => ({ key: `ab_${a.key}`, ability: a.key,
    label: a.weight ? `${a.label} ${a.weight}%` : `${a.label} (참고)`, get: (r) => r.scores[a.key] })),
  { key: 'sets', label: '세트' },
])
const playerLink = (r) => ({ path: `/players/${r.player.code}`, query: { season: data.value.season.code } })
</script>

<template>
  <h1>포지션별 능력치</h1>
  <FilterBar>
    <label class="all small"><input v-model="includeAll" type="checkbox"> 순위 자격 미달 선수도 보기</label>
  </FilterBar>
  <div v-glider class="tabs" role="group" aria-label="포지션">
    <button v-for="p in POSITION_ORDER" :key="p" type="button" class="tab" :aria-pressed="position === p"
            @click="position = p">{{ POSITION_NAMES[p] }}</button>
  </div>

  <AsyncState :loading="state.loading && !data" :error="state.error">
    <div v-if="data" :class="{ busy: state.loading }" :aria-busy="state.loading">
      <p v-if="early" class="early small" role="note">
        시즌 초반이라 10세트 이상 뛴 선수가 아직 적고, 점수도 평균 쪽으로 많이 당겨진 잠정값이에요.
        <button v-if="previousSeason" type="button" class="btn small-btn" @click="filters.set({ season: previousSeason.code })">
          {{ previousSeason.years.replace('-', '–') }} 시즌 보기</button>
      </p>
      <Transition name="swap" mode="out-in">
        <div v-if="top3.length" :key="`${data.season.code}-${data.gender}-${data.position}`" class="podium">
          <RouterLink v-for="(r, i) in top3" :key="r.player.code" v-reveal="i" :to="playerLink(r)" class="mini lift">
            <span class="place">{{ r.rank }}위</span>
            <ScoreRing :value="r.overall" :size="68" />
            <span class="nm"><b>{{ r.player.name }}</b><span class="muted small">{{ r.team.name }}</span>
              <span v-if="strength(r)" class="small best">강점 {{ strength(r) }}</span></span>
          </RouterLink>
        </div>
      </Transition>

      <section class="card">
        <div class="card-head">
          <h2>{{ data.position_name }} <span class="muted small">{{ data.season.years.replace('-', '–') }} 정규리그 ·
            {{ data.rows.filter((r) => r.rank).length }}명</span></h2>
        </div>
        <SortableTable v-if="data.rows.length" :columns="columns" :rows="data.rows" :row-key="(r) => r.player.code"
                       :initial-sort="{ key: 'overall', dir: 'desc' }" :caption="`${data.position_name} 능력치 순위`">
          <template #cell-name="{ row }">
            <RouterLink :to="playerLink(row)">{{ row.player.name }}</RouterLink>
            <span v-if="row.provisional" class="prov" title="출전이 적어 평균 쪽으로 당긴 잠정 점수">잠정</span>
          </template>
          <template #cell-team="{ row }"><RouterLink :to="`/teams/${row.team.code}`">{{ row.team.name }}</RouterLink></template>
          <template v-for="a in data.abilities" :key="a.key" #[`cell-ab_${a.key}`]="{ row }">
            <span class="cell" :title="ABILITY_HELP[a.key]">
              <i class="mini-bar" :class="bandOf(row.scores[a.key])" :style="{ width: `${(row.scores[a.key] || 0) / 99 * 36}px` }" />
              {{ row.scores[a.key] ?? '-' }}
            </span>
          </template>
        </SortableTable>
        <p v-else class="empty">이 시즌 이 포지션에 순위 자격을 갖춘 선수가 없습니다.</p>
        <p class="small muted note">{{ data.scale }} 열 제목을 누르면 그 능력치 순으로 정렬돼요.
          비중(%)은 종합에서의 몫이고, '참고' 항목은 종합에 넣지 않아요.</p>
      </section>
    </div>
  </AsyncState>
</template>

<style scoped>
.all { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; color: var(--color-muted-foreground); }
.all input { width: 16px; height: 16px; accent-color: var(--color-primary); }
.podium { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: var(--space-4); }
.mini { display: flex; align-items: center; gap: 12px; padding: 12px 14px; background: var(--color-card); border: 1px solid var(--color-border);
        border-radius: var(--radius); color: var(--color-foreground); position: relative; }
.mini:hover { text-decoration: none; border-color: var(--color-primary); }
.place { position: absolute; top: 8px; right: 12px; font-size: .75rem; font-weight: 800; color: var(--color-muted-foreground); }
.nm { display: flex; flex-direction: column; min-width: 0; }
.nm b { font-size: 1.05rem; color: var(--color-heading); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.best { color: var(--color-primary); font-weight: 700; }
.prov { margin-left: 6px; font-size: .7rem; font-weight: 700; color: var(--color-accent); background: var(--color-accent-soft);
        border-radius: 999px; padding: 0 6px; }
.cell { display: inline-flex; align-items: center; gap: 6px; justify-content: flex-end; }
.mini-bar { display: inline-block; height: 6px; border-radius: 3px; background: var(--color-muted-foreground); opacity: .5; }
.mini-bar.top { background: var(--color-heading); opacity: 1; }
.mini-bar.high { background: var(--color-primary); opacity: 1; }
.mini-bar.mid { background: color-mix(in srgb, var(--color-primary) 55%, white); opacity: 1; }
.note { margin: var(--space-3) 0 0; }
.early { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; margin: 0 0 var(--space-3);
         padding: 8px 12px; border-radius: 8px; background: var(--color-accent-soft); color: var(--color-foreground); }
.small-btn { min-height: 34px; font-size: .85rem; }
/* short headers stay on one line; the table scrolls inside its card on narrow screens */
:deep(table.data th) { white-space: nowrap; }
@media (max-width: 640px) {
  .podium { grid-template-columns: 1fr; }
}
</style>
