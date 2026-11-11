<script setup>
// 머리글의 검색창과 자동완성(WEB-10). ARIA 1.2 콤보박스 규칙을 따른다(화면 낭독기·키보드 사용자를 위해):
//   입력칸이 팀/선수 목록(listbox)을 가지고, ↑↓로 선택 항목을 옮기고, Enter로 연다(선택이 없으면 전체 검색 화면),
//   Esc로 닫는다(닫혀 있으면 입력 지우기).
// 제안은 입력이 멈추고 200ms 뒤에 요청한다(디바운스: 글자마다 요청하지 않음). 새 요청은 이전 요청을 취소한다.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { suggest } from '@/api/client'
import { GENDERS, POSITIONS } from '@/constants/volley'

const router = useRouter()
const route = useRoute()
const q = ref(route.name === 'search' ? String(route.query.q || '') : '')
const items = ref([])
const open = ref(false)
const active = ref(-1)
const loading = ref(false)
let timer = null // 디바운스 타이머
let controller = null // 진행 중인 요청을 취소하기 위한 AbortController

const listId = 'search-suggest'
const optionId = (i) => `search-opt-${i}`
const expanded = computed(() => open.value && q.value.trim().length > 0)

// 제안 항목 아래 작은 설명: "팀 · 남자부" 또는 "선수 · 소속 팀 · 포지션"
function describe(it) {
  if (it.type === 'team') return `팀 · ${GENDERS[it.gender] || ''}`
  return ['선수', it.team_name, POSITIONS[it.position] || it.position].filter(Boolean).join(' · ')
}

// 입력이 바뀌면 200ms 뒤에 제안을 요청(그 사이 또 바뀌면 타이머를 다시 시작)
watch(q, (v) => {
  clearTimeout(timer)
  active.value = -1
  if (!v.trim()) { items.value = []; return }
  timer = setTimeout(fetchSuggest, 200)
})

async function fetchSuggest() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  try {
    items.value = (await suggest(q.value.trim(), controller.signal)).results || []
    open.value = true
  } catch (e) {
    if (e.name !== 'CanceledError') items.value = [] // 취소된 요청은 오류가 아니다
  } finally {
    loading.value = false
  }
}

// 제안 항목 열기: 팀 화면 또는 선수 화면으로
function go(it) {
  open.value = false
  router.push(it.type === 'team' ? `/teams/${it.code}` : `/players/${it.code}`)
}

// Enter: 선택된 제안이 있으면 그것을, 없으면 전체 검색 화면으로
function submit() {
  const text = q.value.trim()
  if (active.value >= 0 && items.value[active.value]) return go(items.value[active.value])
  if (!text) return
  open.value = false
  router.push({ name: 'search', query: { q: text } })
}

function onKey(e) {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!items.value.length) return
    e.preventDefault()
    open.value = true
    const n = items.value.length
    active.value = e.key === 'ArrowDown' ? (active.value + 1) % n : (active.value - 1 + n) % n
  } else if (e.key === 'Escape') {
    if (open.value) { e.preventDefault(); open.value = false; active.value = -1 } else q.value = ''
  }
}

// 포커스가 위젯 전체를 벗어나면 닫는다(항목을 클릭할 때는 클릭이 끝날 때까지 열어 둠)
function onFocusOut(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) open.value = false
}

// 다른 화면으로 가면 닫고, 검색 화면이 아니면 입력도 비운다
watch(() => route.fullPath, () => { open.value = false; if (route.name !== 'search') q.value = '' })
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort() })
</script>

<template>
  <form class="search" role="search" @submit.prevent="submit" @focusout="onFocusOut">
    <label for="site-search" class="sr-only">팀, 선수, 기사 검색</label>
    <svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="6" /><path d="m14 14 4 4" /></svg>
    <input id="site-search" v-model="q" type="search" autocomplete="off" placeholder="팀·선수·기사 검색"
           role="combobox" aria-autocomplete="list" :aria-expanded="expanded" :aria-controls="listId"
           :aria-activedescendant="active >= 0 ? optionId(active) : undefined"
           @keydown="onKey" @focus="open = items.length > 0" />
    <Transition name="pop">
    <ul v-show="expanded" :id="listId" role="listbox" class="list" aria-label="검색 추천">
      <li v-for="(it, i) in items" :id="optionId(i)" :key="`${it.type}-${it.code}`" role="option"
          :aria-selected="i === active" class="option" :class="{ active: i === active }"
          @mousedown.prevent @click="go(it)" @mousemove="active = i">
        <span class="name">{{ it.name }}</span>
        <span class="desc">{{ describe(it) }}</span>
      </li>
      <li v-if="!items.length && !loading" class="none" role="presentation">추천 결과가 없습니다</li>
      <li role="presentation">
        <button type="submit" class="all" tabindex="-1" @mousedown.prevent>'{{ q.trim() }}' 기사까지 전체 검색</button>
      </li>
    </ul>
    </Transition>
  </form>
</template>

<style scoped>
.search { position: relative; margin-left: auto; width: min(280px, 32vw); }
.icon { position: absolute; left: 10px; top: 50%; width: 16px; height: 16px; transform: translateY(-50%);
        fill: none; stroke: var(--search-muted, var(--color-muted-foreground)); stroke-width: 1.8; pointer-events: none; }
.search:focus-within .icon { stroke: var(--color-muted-foreground); }
input { width: 100%; font: inherit; font-size: .9rem; padding: 7px 10px 7px 32px; min-height: 36px; border-radius: 999px;
        border: 1px solid var(--search-border, var(--color-border)); background: var(--search-bg, var(--color-muted));
        color: var(--search-fg, var(--color-foreground));
        transition: border-color .15s var(--ease), background-color .15s var(--ease), color .15s var(--ease); }
/* 색은 --search-* 변수로 받는다: 어두운 머리글 안에서는 App.vue가 밝은 글자색을 넘겨주고, 입력을 시작하면(포커스) 흰 바탕으로 바뀐다 */
input:hover { border-color: var(--search-fg, var(--color-primary)); }
input:focus { background: var(--color-card); border-color: var(--color-card); color: var(--color-foreground); }
input::placeholder { color: var(--search-muted, var(--color-muted-foreground)); }
input:focus::placeholder { color: var(--color-muted-foreground); }
.list { position: absolute; top: calc(100% + 6px); left: 0; right: 0; min-width: 260px; margin: 0; padding: 4px; list-style: none;
        background: var(--color-card); border: 1px solid var(--color-border); border-radius: var(--radius);
        box-shadow: 0 8px 24px rgb(15 23 42 / .12); z-index: 20; }
.option { display: flex; justify-content: space-between; gap: 8px; padding: 8px 10px; border-radius: 6px; cursor: pointer; min-height: 40px; align-items: center;
          transition: background-color 90ms linear; }
.option.active { background: var(--color-primary-soft); }
.name { font-weight: 500; }
.desc { color: var(--color-muted-foreground); font-size: .8rem; white-space: nowrap; }
.none { padding: 8px 10px; color: var(--color-muted-foreground); font-size: .85rem; }
.all { all: unset; display: block; width: 100%; box-sizing: border-box; padding: 8px 10px; margin-top: 2px; border-top: 1px solid var(--color-border-soft);
       color: var(--color-primary); font-size: .85rem; cursor: pointer; border-radius: 0 0 6px 6px; }
.all:hover { background: var(--color-primary-soft); }
@media (pointer: coarse) { input { min-height: 44px; } .option { min-height: 44px; } }
/* own row under the menu, sharing it with the account link (App.vue) */
@media (max-width: 900px) { .search { width: auto; flex: 1 1 calc(100% - 160px); margin-left: 0; } }
</style>
