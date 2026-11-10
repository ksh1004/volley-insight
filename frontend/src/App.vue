<script setup>
// 앱의 뼈대: 머리글(로고, 주 메뉴, 검색창, 로그인/내 정보), 본문(지금 주소의 화면), 바닥글(데이터 출처).
// 모든 화면이 이 안에 그려진다. <style>에는 전역 디자인 토큰(색·간격)과 공용 클래스(.card, .tabs, .data 표 등)가 있다.
import { RouterLink, RouterView } from 'vue-router'
import SearchBox from '@/components/SearchBox.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// 주 메뉴(exact: 홈은 주소가 정확히 '/'일 때만 선택 표시)
const NAV = [
  { to: '/', label: '홈', exact: true },
  { to: '/schedule', label: '일정·결과' },
  { to: '/standings', label: '순위' },
  { to: '/news', label: '기사' },
  { to: '/predictions', label: '예측 검증' },
]
</script>

<template>
  <!-- 키보드 사용자가 메뉴를 건너뛰고 바로 본문으로 갈 수 있는 링크(Tab을 처음 누르면 보임) -->
  <a class="skip-link" href="#main">본문 바로가기</a>
  <header class="app-header">
    <div class="wrap header-row">
      <RouterLink to="/" class="brand" aria-label="배구 전력 분석 홈">
        <svg class="logo" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <path d="M4 9c6 0 11 3 15 9M8 3c1 6 6 11 14 11M3 15c5-3 11-3 16-11" />
        </svg>
        <span>배구 전력 분석</span>
      </RouterLink>
      <!-- 선택된 메뉴 밑줄이 미끄러져 이동(v-glider:underline) -->
      <nav v-glider:underline="'.is-active'" class="nav" aria-label="주 메뉴">
        <RouterLink v-for="n in NAV" :key="n.to" :to="n.to" class="nav-link"
                    :exact-active-class="n.exact ? 'is-active' : ''" :active-class="n.exact ? '' : 'is-active'">{{ n.label }}</RouterLink>
      </nav>
      <SearchBox />
      <RouterLink v-if="auth.isLoggedIn" to="/me" class="account" active-class="is-active"
                  :aria-label="`내 정보: ${auth.user?.nickname || ''}`">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>
        <span class="who">{{ auth.user?.nickname || '내 정보' }}</span>
      </RouterLink>
      <RouterLink v-else :to="{ path: '/login', query: { next: $route.fullPath } }" class="account">로그인</RouterLink>
    </div>
  </header>
  <main id="main" class="wrap app-main" tabindex="-1">
    <!-- 화면 전환 효과: 주소(fullPath)가 바뀌면 이전 화면이 사라진 뒤(out-in) 새 화면이 나타난다.
         키가 전체 주소라서 같은 화면이라도 쿼리가 바뀌면 다시 만들어진다(그래서 일부 화면은 history.replaceState를 씀) -->
    <RouterView v-slot="{ Component, route }">
      <Transition name="page" mode="out-in">
        <div :key="route.fullPath" class="page"><component :is="Component" /></div>
      </Transition>
    </RouterView>
  </main>
  <footer class="app-footer">
    <div class="wrap">
      데이터 출처: <a href="https://www.kovo.co.kr" target="_blank" rel="noopener">KOVO 한국배구연맹</a>.
      비상업 학습용 프로젝트이며, 경기 기록 사실 정보만 사용합니다. 기사는 각 언론사의 제목·요약·링크만 보여줍니다.
    </div>
  </footer>
</template>

<style>
/* 디자인 토큰(색, 간격, 둥글기 등): design-system/volley-insight/MASTER.md와 같은 값.
   컴포넌트는 색 값을 직접 쓰지 않고 이 변수(var(--color-...))만 쓴다 → 색을 바꿀 때 여기만 고치면 된다 */
:root {
  --color-primary: #1e40af;
  --color-primary-soft: #eff4ff;
  --color-accent: #b45309;
  --color-accent-soft: #fff7ed;
  --color-background: #f8fafc;
  --color-card: #ffffff;
  --color-foreground: #0f172a;
  --color-heading: #1e3a8a;
  --color-muted-foreground: #475569;
  --color-muted: #f1f5f9;
  --color-border: #e2e8f0;
  --color-border-soft: #eef2f6;
  --color-border-strong: #dbeafe;
  --color-good: #15803d;
  --color-bad: #b91c1c;
  --color-home: var(--color-primary);
  --color-away: var(--color-accent);
  --color-ring: #1e40af;
  --color-highlight: #fef3c7; /* search match marks */

  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --radius: 10px;
  --shadow-card: 0 1px 2px rgb(15 23 42 / .04);
  --ease: cubic-bezier(.2, .7, .2, 1);
  --font-sans: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', system-ui, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-sans); color: var(--color-foreground); background: var(--color-background);
       font-size: 15px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
a { color: var(--color-primary); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 3px; }
h1 { font-size: 1.5rem; margin: 0 0 var(--space-4); color: var(--color-heading); letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 0 0 var(--space-3); color: var(--color-heading); }
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 var(--space-4); }

/* keyboard focus is always visible */
:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; border-radius: 4px; }
.skip-link { position: absolute; left: -9999px; top: 8px; z-index: 100; background: var(--color-primary); color: #fff;
             padding: var(--space-2) var(--space-3); border-radius: 6px; }
.skip-link:focus { left: 8px; }

.app-header { background: var(--color-card); border-bottom: 1px solid var(--color-border); position: sticky; top: 0; z-index: 10; }
.header-row { display: flex; align-items: center; gap: var(--space-5); height: 56px; }
.brand { display: flex; align-items: center; gap: var(--space-2); font-weight: 700; font-size: 1.02rem; color: var(--color-heading); }
.brand:hover { text-decoration: none; }
.logo { width: 24px; height: 24px; fill: var(--color-primary); stroke: #fff; stroke-width: 1.6; }
.nav { display: flex; gap: var(--space-1); height: 100%; }
.nav-link { display: flex; align-items: center; padding: 0 var(--space-3); color: var(--color-muted-foreground); font-weight: 500;
            border-bottom: 2px solid transparent; transition: color .15s var(--ease), border-color .15s var(--ease); }
.nav-link:hover { color: var(--color-foreground); text-decoration: none; }
.nav-link.is-active { color: var(--color-primary); border-bottom-color: var(--color-primary); font-weight: 700; }
.nav.glider-ready .nav-link.is-active { border-bottom-color: transparent; } /* the sliding underline (v-glider) draws it */
.account { display: flex; align-items: center; gap: 6px; flex-shrink: 0; min-height: 36px; padding: 0 12px; border-radius: 999px;
           border: 1px solid var(--color-border); color: var(--color-foreground); font-size: .88rem; font-weight: 600; white-space: nowrap; }
.account:hover { border-color: var(--color-primary); color: var(--color-primary); text-decoration: none; }
.account.is-active { border-color: var(--color-primary); color: var(--color-primary); background: var(--color-primary-soft); }
.account svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; }
.account .who { max-width: 8em; overflow: hidden; text-overflow: ellipsis; }
.app-main { padding-top: var(--space-5); padding-bottom: 48px; min-height: calc(100vh - 140px); outline: none; }
.app-footer { border-top: 1px solid var(--color-border); background: var(--color-card); color: var(--color-muted-foreground); font-size: .85rem; padding: var(--space-4) 0; }
@media (max-width: 760px) {
  /* search moves to its own row under the menu */
  .header-row { flex-wrap: wrap; height: auto; row-gap: 0; padding-bottom: var(--space-2); }
  .header-row > .brand, .header-row > .nav { height: 48px; }
  .account { min-height: 40px; }
  .account .who { max-width: 5em; }
}
@media (max-width: 560px) {
  .header-row { gap: var(--space-2); }
  .brand span { display: none; }
  .nav-link { padding: 0 var(--space-2); }
}

/* shared building blocks */
.card { background: var(--color-card); border: 1px solid var(--color-border); border-radius: var(--radius);
        padding: var(--space-4); margin-bottom: var(--space-4); box-shadow: var(--shadow-card); }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); flex-wrap: wrap; margin-bottom: var(--space-3); }
.card-head h2 { margin: 0; }
.muted { color: var(--color-muted-foreground); }
.small { font-size: .85rem; }
.num { font-variant-numeric: tabular-nums; }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.grid-2 > * { min-width: 0; } /* a wide table scrolls inside its card instead of widening the page */
@media (max-width: 760px) { .grid-2 { grid-template-columns: 1fr; } }

.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table.data { width: 100%; border-collapse: collapse; font-size: .9rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
table.data th, table.data td { padding: 7px 10px; border-bottom: 1px solid var(--color-border-soft); text-align: right; }
table.data th { color: var(--color-muted-foreground); font-weight: 500; font-size: .8rem; background: var(--color-muted);
                white-space: normal; line-height: 1.3; vertical-align: bottom; min-width: 3.2em; } /* long headers wrap instead of widening the table */
table.data .left { text-align: left; }
table.data td.strong { font-weight: 700; color: var(--color-heading); }
table.data tbody tr { transition: background-color .12s var(--ease); }
table.data tbody tr:hover td { background: var(--color-primary-soft); }
table.data .sticky { position: sticky; left: 0; background: var(--color-card); z-index: 1; }
table.data th.sticky { background: var(--color-muted); }
table.data th.sortable { cursor: pointer; user-select: none; }
table.data th.sortable button { all: unset; cursor: pointer; }
table.data th.sortable button:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
table.data th[aria-sort] { color: var(--color-primary); font-weight: 700; }

.tabs { display: flex; gap: var(--space-1); flex-wrap: wrap; margin-bottom: var(--space-3); }
.tab { border: 1px solid var(--color-border); background: var(--color-card); color: var(--color-muted-foreground); padding: 6px 12px;
       border-radius: 999px; cursor: pointer; font: inherit; font-size: .88rem; min-height: 34px;
       transition: background-color .15s var(--ease), color .15s var(--ease), border-color .15s var(--ease); }
.tab:hover { border-color: var(--color-primary); color: var(--color-primary); }
.tab.active, .tab[aria-pressed='true'] { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
select { font: inherit; padding: 6px 10px; min-height: 34px; border: 1px solid var(--color-border); border-radius: 8px;
         background: var(--color-card); color: var(--color-foreground); cursor: pointer; }
select:hover { border-color: var(--color-primary); }
@media (pointer: coarse) { .tab, select { min-height: 44px; } }

.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .75rem; font-weight: 700; line-height: 1.6; }
.badge.w { background: var(--color-primary-soft); color: var(--color-primary); }
.badge.l { background: var(--color-muted); color: var(--color-muted-foreground); }
.badge.today { background: var(--color-accent-soft); color: var(--color-accent); }
.empty { color: var(--color-muted-foreground); padding: var(--space-5) 0; text-align: center; }

/* buttons and form fields (accounts) */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 40px; padding: 0 16px;
       border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card); color: var(--color-foreground);
       font: inherit; font-size: .92rem; font-weight: 600; cursor: pointer;
       transition: background-color .15s var(--ease), color .15s var(--ease), border-color .15s var(--ease); }
.btn:hover { border-color: var(--color-primary); color: var(--color-primary); text-decoration: none; }
.btn.primary { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.btn.primary:hover { background: var(--color-heading); color: #fff; }
.btn.danger { color: var(--color-bad); border-color: #fecaca; }
.btn.danger:hover { background: #fef2f2; border-color: var(--color-bad); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.field { display: grid; gap: 6px; margin-bottom: var(--space-4); }
.field > label { font-weight: 600; font-size: .9rem; }
.field .hint { font-size: .8rem; color: var(--color-muted-foreground); margin: 0; }
.field .err { font-size: .82rem; color: var(--color-bad); margin: 0; }
.input { width: 100%; min-height: 44px; padding: 8px 12px; font: inherit; color: var(--color-foreground); background: var(--color-card);
         border: 1px solid var(--color-border); border-radius: 8px; transition: border-color .15s var(--ease); }
.input:hover { border-color: var(--color-primary); }
.input[aria-invalid='true'] { border-color: var(--color-bad); }
@media (pointer: coarse) { .btn { min-height: 44px; } }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0ms !important; animation-duration: 0ms !important; animation-iteration-count: 1 !important; }
}
</style>
