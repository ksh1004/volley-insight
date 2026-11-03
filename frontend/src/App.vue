<script setup>
import { RouterLink, RouterView } from 'vue-router'

const NAV = [
  { to: '/', label: '홈', exact: true },
  { to: '/schedule', label: '일정·결과' },
  { to: '/standings', label: '순위' },
]
</script>

<template>
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
      <nav class="nav" aria-label="주 메뉴">
        <RouterLink v-for="n in NAV" :key="n.to" :to="n.to" class="nav-link"
                    :exact-active-class="n.exact ? 'is-active' : ''" :active-class="n.exact ? '' : 'is-active'">{{ n.label }}</RouterLink>
      </nav>
    </div>
  </header>
  <main id="main" class="wrap app-main" tabindex="-1">
    <RouterView :key="$route.fullPath" />
  </main>
  <footer class="app-footer">
    <div class="wrap">
      데이터 출처: <a href="https://www.kovo.co.kr" target="_blank" rel="noopener">KOVO 한국배구연맹</a>.
      비상업 학습용 프로젝트이며, 경기 기록 사실 정보만 사용합니다.
    </div>
  </footer>
</template>

<style>
/* Design tokens: design-system/volley-insight/MASTER.md */
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
.app-main { padding-top: var(--space-5); padding-bottom: 48px; min-height: calc(100vh - 140px); outline: none; }
.app-footer { border-top: 1px solid var(--color-border); background: var(--color-card); color: var(--color-muted-foreground); font-size: .85rem; padding: var(--space-4) 0; }
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

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0ms !important; animation-duration: 0ms !important; animation-iteration-count: 1 !important; }
}
</style>
