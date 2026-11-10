<script setup>
// Season select (hide-season leaves it out) + men/women toggle, bound to the shared filters store.
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
