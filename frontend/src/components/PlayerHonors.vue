<script setup>
// 선수의 수상·주요 기록(COL-04): 수상 / 트리플크라운 / 기록 달성을 세 칸으로, 각각 최근 것부터.
// 데이터는 KOVO 선수 화면의 "수상 내역" 문장 그대로(시즌만 떼어 왼쪽 칸에 둠). 목록이 길면(예: 수상 40건) 앞의 6건만
// 보여 주고 "모두 보기"로 펼친다.
import { computed, reactive } from 'vue'

const props = defineProps({ honors: { type: Object, required: true } }) // { awards, triple_crowns, records }
const SHOWN = 6

const groups = computed(() => [
  { key: 'awards', label: '수상' },
  { key: 'triple_crowns', label: '트리플크라운' },
  { key: 'records', label: '기록 달성' },
].map((g) => ({ ...g, items: props.honors[g.key] || [] })).filter((g) => g.items.length))
const open = reactive({})

// "2025-2026" → "2025–26"(좁은 칸에 맞게 줄임)
const shortSeason = (s) => (s && /^\d{4}-\d{4}$/.test(s) ? `${s.slice(0, 4)}–${s.slice(7)}` : s || '')
</script>

<template>
  <div class="honors">
    <section v-for="g in groups" :key="g.key" class="group" :aria-labelledby="`honor-${g.key}`">
      <h3 :id="`honor-${g.key}`"><span>{{ g.label }}</span><span class="count">{{ g.items.length }}</span></h3>
      <ol :id="`honor-list-${g.key}`">
        <li v-for="(h, i) in open[g.key] ? g.items : g.items.slice(0, SHOWN)" :key="i">
          <span class="season">{{ shortSeason(h.season) }}</span>
          <span class="title">{{ h.title }}</span>
        </li>
      </ol>
      <button v-if="g.items.length > SHOWN" type="button" class="more" :aria-expanded="!!open[g.key]"
              :aria-controls="`honor-list-${g.key}`" @click="open[g.key] = !open[g.key]">
        {{ open[g.key] ? '접기' : `모두 보기 (${g.items.length}건)` }}
      </button>
    </section>
  </div>
</template>

<style scoped>
.honors { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap: var(--space-lg) var(--gap-columns); }
.group { min-width: 0; }
h3 { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-xs); margin: 0;
     padding-bottom: var(--space-xs); border-bottom: var(--rule-bar) solid var(--color-rule-2); }
.count { font-family: var(--font-display); font-size: var(--text-2xl); line-height: 1; font-variant-numeric: tabular-nums; }
ol { list-style: none; margin: 0; padding: 0; }
li { display: grid; grid-template-columns: 4.6em minmax(0, 1fr); gap: var(--space-sm); padding: var(--space-xs) 0;
     border-bottom: var(--rule-hair) solid var(--color-rule); font-size: var(--text-sm); line-height: 1.45; }
.season { font-family: var(--font-outlier); color: var(--color-ink-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.title { word-break: keep-all; overflow-wrap: break-word; } /* 한글 단어 중간에서 줄을 바꾸지 않는다 */
.more { margin-top: var(--space-xs); padding: 0; min-height: 32px; border: 0; background: none; color: var(--color-ink); cursor: pointer;
        font-size: var(--text-sm); font-weight: 700; text-decoration: underline; text-decoration-color: var(--color-rule);
        text-underline-offset: 0.2em; white-space: nowrap; }
.more:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.more:hover { text-decoration-color: var(--color-volt-deep); text-decoration-thickness: 2px; }
.more:active { color: var(--color-ink-2); }
@media (pointer: coarse) { .more { min-height: 44px; } }
</style>
