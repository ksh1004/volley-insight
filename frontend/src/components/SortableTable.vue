<script setup>
// Data table whose columns sort on click or Enter/Space (headers are buttons; aria-sort reports the order).
// columns: [{ key, label, left?, sticky?, strong?, sortable? (default true), get?(row) sort value, fmt?(row) display }]
// A named slot `cell-<key>` (props: { row }) replaces a cell, e.g. to render a link.
import { computed, ref } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, required: true },
  rowKey: { type: Function, required: true },
  initialSort: { type: Object, default: () => ({ key: null, dir: 'desc' }) },
  caption: { type: String, default: '' },
})

const sort = ref({ ...props.initialSort })
const valueOf = (col, row) => (col.get ? col.get(row) : row[col.key])

const sorted = computed(() => {
  const col = props.columns.find((c) => c.key === sort.value.key)
  if (!col) return props.rows
  const dir = sort.value.dir === 'asc' ? 1 : -1
  return [...props.rows].sort((a, b) => {
    const va = valueOf(col, a)
    const vb = valueOf(col, b)
    if (va == null && vb == null) return 0
    if (va == null) return 1 // empty values last in both directions
    if (vb == null) return -1
    return (typeof va === 'string' ? va.localeCompare(vb, 'ko') : va - vb) * dir
  })
})

function toggle(col) {
  if (col.sortable === false) return
  sort.value = sort.value.key === col.key
    ? { key: col.key, dir: sort.value.dir === 'desc' ? 'asc' : 'desc' }
    : { key: col.key, dir: col.left ? 'asc' : 'desc' } // names A→Z, numbers high→low first
}
const ariaSort = (col) => (sort.value.key === col.key ? (sort.value.dir === 'asc' ? 'ascending' : 'descending') : undefined)
</script>

<template>
  <div class="table-wrap">
    <table class="data">
      <caption v-if="caption" class="sr-only">{{ caption }}</caption>
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.key" scope="col"
              :class="{ left: col.left, sticky: col.sticky, sortable: col.sortable !== false }" :aria-sort="ariaSort(col)">
            <button v-if="col.sortable !== false" type="button" @click="toggle(col)">
              {{ col.label }}<span class="arrow" aria-hidden="true">{{ ariaSort(col) === 'ascending' ? '▲' : ariaSort(col) ? '▼' : '' }}</span>
            </button>
            <template v-else>{{ col.label }}</template>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in sorted" :key="rowKey(row)">
          <td v-for="col in columns" :key="col.key" :class="{ left: col.left, sticky: col.sticky, strong: col.strong }">
            <slot :name="`cell-${col.key}`" :row="row">{{ col.fmt ? col.fmt(row) : (valueOf(col, row) ?? '-') }}</slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.arrow { display: inline-block; width: 1em; font-size: .65rem; margin-left: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
