<script setup>
// 필터 막대: 시즌 선택 상자(hide-season이면 숨김) + 남자부/여자부 탭. 공용 필터 저장소(stores/filters.js)와 연결되어
// 여기서 바꾸면 그 값을 쓰는 모든 화면이 함께 바뀐다. all-genders면 '전체' 탭도 보인다.
// <slot />: 이 막대 오른쪽에 화면마다 다른 요소(체크박스 등)를 끼워 넣을 수 있다.
import { GENDERS } from '@/constants/volley'
import { useFiltersStore } from '@/stores/filters'

defineProps({ allGenders: Boolean, hideSeason: Boolean })
const filters = useFiltersStore()
</script>

<template>
  <div class="filter-bar">
    <select v-if="!hideSeason" :value="filters.season" aria-label="시즌" @change="filters.set({ season: $event.target.value })">
      <option v-for="s in filters.seasons" :key="s.code" :value="s.code">{{ s.years.replace('-', '–') }} 시즌</option>
    </select>
    <div v-glider class="tabs" role="group" aria-label="남녀부">
      <button v-if="allGenders" type="button" class="tab" :aria-pressed="filters.gender === ''" @click="filters.set({ gender: '' })">전체</button>
      <button v-for="(label, g) in GENDERS" :key="g" type="button" class="tab" :aria-pressed="filters.gender === g"
              @click="filters.set({ gender: g })">{{ label }}</button>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; margin-bottom: 16px; }
.filter-bar .tabs { margin: 0; }
</style>
