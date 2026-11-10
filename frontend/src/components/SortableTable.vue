<script setup>
// 열 제목을 누르면(또는 Enter/Space) 정렬되는 표. 제목은 버튼이고, aria-sort로 정렬 상태를 화면 낭독기에 알린다.
// columns: [{ key, label, left?(왼쪽 정렬), sticky?(가로 스크롤 때 고정), strong?(굵게), sortable?(기본 true),
//             get?(row) 정렬에 쓸 값, fmt?(row) 화면에 보일 글자 }]
// 이름 있는 슬롯 `cell-<key>`(props: { row })로 칸 하나를 바꿔 그릴 수 있다(예: 링크).
// 모션: 다시 정렬하면 행이 새 자리로 미끄러져 간다(Vue TransitionGroup이 transform으로 옮김). 그래서 선수나 팀이
// 어디로 갔는지 눈으로 따라갈 수 있다.
import { computed, ref } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, required: true },
  rowKey: { type: Function, required: true },
  initialSort: { type: Object, default: () => ({ key: null, dir: 'desc' }) },
  caption: { type: String, default: '' },
})

const sort = ref({ ...props.initialSort }) // { key: 정렬 열, dir: 'asc' | 'desc' }
const valueOf = (col, row) => (col.get ? col.get(row) : row[col.key])

// 정렬된 행들(원본 배열은 건드리지 않고 복사해서 정렬)
const sorted = computed(() => {
  const col = props.columns.find((c) => c.key === sort.value.key)
  if (!col) return props.rows
  const dir = sort.value.dir === 'asc' ? 1 : -1
  return [...props.rows].sort((a, b) => {
    const va = valueOf(col, a)
    const vb = valueOf(col, b)
    if (va == null && vb == null) return 0
    if (va == null) return 1 // 빈 값은 오름차순·내림차순 모두 맨 뒤로
    if (vb == null) return -1
    return (typeof va === 'string' ? va.localeCompare(vb, 'ko') : va - vb) * dir
  })
})

// 같은 열을 다시 누르면 방향 반전, 다른 열이면 그 열로
function toggle(col) {
  if (col.sortable === false) return
  sort.value = sort.value.key === col.key
    ? { key: col.key, dir: sort.value.dir === 'desc' ? 'asc' : 'desc' }
    : { key: col.key, dir: col.left ? 'asc' : 'desc' } // 글자 열은 가나다순, 숫자 열은 큰 값부터
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
      <TransitionGroup tag="tbody" name="row">
        <tr v-for="row in sorted" :key="rowKey(row)">
          <td v-for="col in columns" :key="col.key" :class="{ left: col.left, sticky: col.sticky, strong: col.strong }">
            <slot :name="`cell-${col.key}`" :row="row">{{ col.fmt ? col.fmt(row) : (valueOf(col, row) ?? '-') }}</slot>
          </td>
        </tr>
      </TransitionGroup>
    </table>
  </div>
</template>

<style scoped>
.arrow { display: inline-block; width: 1em; font-size: .65rem; margin-left: 2px; }
</style>
