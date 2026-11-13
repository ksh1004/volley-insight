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
/* Hallmark · shape: app screen · genre: editorial · theme: Sport (deal 1/3, seed live-sports-broadcast-scoreboard-05a1·7a998a35·2026-11-11·r0) · redesign: full
 * nav: kept top bar, now an ink band with a volt bar under the current page · signature: the fixture board (today's matches as ruled score tiles) · states: loading, empty, error, full
 * axes: light/display-condensed/chromatic-volt · posture: restrained · contrast: pass · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H4 E3 S4 R4 V4 · weakest: E, the board carries the first screen so type scale does the work a picture would */
/* Hallmark · waive 64 · times and figures are set in mono because the theme's material is a results sheet; labels stay in the body face */
@import url("./tokens.css");
@import url("./base.css");

/* 전역 스타일: 머리글·바닥글과, 모든 화면이 함께 쓰는 공용 클래스(.card, .section, .tabs, table.data, .btn 등).
   모습의 기준은 "경기 기록지": 상자 대신 줄(굵은 줄로 열고 닫고, 가는 줄로 행을 나눔), 그림자 없음, 강조는 형광 라임 채움 하나. */
h1 { margin: 0 0 var(--space-md); }
h2 { margin: 0 0 var(--space-sm); }
.wrap { max-width: var(--container); margin: 0 auto; padding: 0 var(--gutter); }

.skip-link { position: absolute; left: -9999px; top: 8px; z-index: var(--z-modal); background: var(--color-volt); color: var(--color-volt-ink);
             padding: var(--space-xs) var(--space-sm); border-radius: var(--radius-card); font-weight: 700; text-decoration: none; }
.skip-link:focus { left: 8px; }

/* 머리글: 잉크색 띠. 지금 화면의 메뉴 아래에는 라임색 굵은 줄이 미끄러져 온다(v-glider).
   안쪽 검색창은 --search-* 변수로 어두운 바탕용 색을 받는다(SearchBox.vue) */
.app-header { background: var(--color-ink); color: var(--color-paper); position: sticky; top: 0; z-index: var(--z-sticky-nav);
              --on-ink-soft: color-mix(in oklab, var(--color-paper) 72%, transparent);
              --search-bg: color-mix(in oklab, var(--color-paper) 10%, transparent);
              --search-border: color-mix(in oklab, var(--color-paper) 22%, transparent);
              --search-fg: var(--color-paper); --search-muted: var(--on-ink-soft); }
.app-header :focus-visible { outline-color: var(--color-volt); }
.header-row { display: flex; align-items: center; gap: var(--space-lg); height: 60px; }
.brand { display: flex; align-items: center; gap: var(--space-xs); color: var(--color-paper); text-decoration: none;
         font-family: var(--font-display); font-weight: var(--display-weight); font-synthesis: none; font-size: var(--text-xl); line-height: 1;
         letter-spacing: var(--tracking-display); white-space: nowrap; }
.logo { width: 26px; height: 26px; fill: var(--color-volt); stroke: var(--color-ink); stroke-width: 1.6; }
.nav { display: flex; gap: var(--space-2xs); height: 100%; }
.nav-link { display: flex; align-items: center; flex: none; padding: 0 var(--space-sm); white-space: nowrap; text-decoration: none;
            color: var(--on-ink-soft); font-weight: 500; border-bottom: 4px solid transparent; border-top: 4px solid transparent;
            transition: color var(--dur-micro) var(--ease-out); }
.nav-link:hover { color: var(--color-paper); }
.nav-link:active { color: var(--color-volt); }
.nav-link.is-active { color: var(--color-paper); border-bottom-color: var(--color-volt); font-weight: 700; }
.nav.glider-ready .nav-link.is-active { border-bottom-color: transparent; } /* 미끄러지는 줄(v-glider)이 대신 그린다 */
.app-header .nav.has-glider > .glider.underline { background: var(--color-volt); height: 4px; border-radius: 0; }
.account { display: flex; align-items: center; gap: var(--space-xs); flex-shrink: 0; min-height: 36px; padding: 0 var(--space-sm);
           border: var(--rule-bar) solid var(--color-paper); border-radius: var(--radius-card); color: var(--color-paper);
           font-size: var(--text-sm); font-weight: 700; white-space: nowrap; text-decoration: none;
           transition: background-color var(--dur-micro) var(--ease-out), color var(--dur-micro) var(--ease-out), border-color var(--dur-micro) var(--ease-out); }
.account:hover { background: var(--color-paper); color: var(--color-ink); }
.account:active, .account.is-active { background: var(--color-volt); border-color: var(--color-volt); color: var(--color-volt-ink); }
.account svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; }
.account .who { max-width: 8em; overflow: hidden; text-overflow: ellipsis; }
.app-main { padding-top: var(--space-lg); padding-bottom: var(--space-2xl); min-height: calc(100vh - 150px); outline: none; }
.app-footer { border-top: var(--rule-bar) solid var(--color-rule-2); background: var(--color-paper-2); color: var(--color-ink-muted);
              font-size: var(--text-sm); padding: var(--space-md) 0; }
@media (max-width: 900px) {
  /* 태블릿·휴대폰: 첫 줄은 로고 + 메뉴, 검색창과 로그인은 둘째 줄로 내려간다.
     메뉴가 줄 폭보다 길면 줄바꿈 대신 메뉴 안에서만 옆으로 민다 */
  .header-row { flex-wrap: wrap; height: auto; row-gap: 0; padding-bottom: var(--space-xs); }
  .header-row > .brand, .header-row > .nav { height: 52px; }
  .nav { flex: 1 1 calc(100% - 200px); min-width: 0; overflow-x: auto; scrollbar-width: none; }
  .nav::-webkit-scrollbar { display: none; }
  .account { min-height: 40px; }
  .account .who { max-width: 5em; }
}
@media (max-width: 560px) {
  .header-row { gap: var(--space-xs); }
  .brand span { display: none; }
  .nav { flex-basis: calc(100% - 50px); } /* 로고 글자가 숨겨져 메뉴가 쓸 자리가 늘어난다 */
  .nav-link { padding: 0 var(--space-xs); }
}

/* 팀 이름은 팀 컬러로: 팀 화면으로 가는 링크는 주소의 팀 코드로, 링크가 아닌 이름은 data-team 표시로 칠한다.
   짙은 바탕(진행 중 경기 타일)과 홈/원정 색이 그래프와 짝을 이루는 곳(실시간 화면)에는 붙이지 않는다 */
a[href^="/teams/1001"], [data-team="1001"] { color: var(--team-1001); }
a[href^="/teams/1002"], [data-team="1002"] { color: var(--team-1002); }
a[href^="/teams/1004"], [data-team="1004"] { color: var(--team-1004); }
a[href^="/teams/1005"], [data-team="1005"] { color: var(--team-1005); }
a[href^="/teams/1006"], [data-team="1006"] { color: var(--team-1006); }
a[href^="/teams/1008"], [data-team="1008"] { color: var(--team-1008); }
a[href^="/teams/1009"], [data-team="1009"] { color: var(--team-1009); }
a[href^="/teams/2001"], [data-team="2001"] { color: var(--team-2001); }
a[href^="/teams/2002"], [data-team="2002"] { color: var(--team-2002); }
a[href^="/teams/2003"], [data-team="2003"] { color: var(--team-2003); }
a[href^="/teams/2004"], [data-team="2004"] { color: var(--team-2004); }
a[href^="/teams/2005"], [data-team="2005"] { color: var(--team-2005); }
a[href^="/teams/2006"], [data-team="2006"] { color: var(--team-2006); }
a[href^="/teams/2008"], [data-team="2008"] { color: var(--team-2008); }

/* ---- 공용 구성 요소 ---- */
/* 카드: 상자가 아니라 "굵은 줄 아래의 구역". 표·글 목록처럼 내용이 한 덩어리일 때 쓴다 */
.card { border-top: var(--rule-bar) solid var(--color-rule-2); padding: var(--space-md) 0 0; margin-bottom: var(--space-xl); }
.card-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-sm); flex-wrap: wrap; margin-bottom: var(--space-sm); }
.card-head h2 { margin: 0; }
/* 구역: 줄 없이 제목만 있는 묶음. 안에 타일 목록(경기 타일 등)이 들어갈 때 쓴다(타일이 저마다 굵은 줄을 가지므로) */
.section { margin-bottom: var(--space-xl); }
.section-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-sm); flex-wrap: wrap; margin-bottom: var(--space-sm); }
.section-head h2 { margin: 0; }
.muted { color: var(--color-ink-muted); }
.small { font-size: var(--text-sm); }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 var(--gap-columns); }
.grid-2 > * { min-width: 0; } /* 넓은 표가 화면을 넓히지 않고 카드 안에서만 가로로 밀리게 */
@media (max-width: 760px) { .grid-2 { grid-template-columns: minmax(0, 1fr); } }

/* 표: 굵은 줄로 열고 닫고, 행은 가는 줄. 숫자는 고정폭(mono)이라 자릿수가 세로로 맞는다 */
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table.data { width: 100%; border-collapse: collapse; font-size: var(--text-sm); white-space: nowrap; font-variant-numeric: tabular-nums;
             border-bottom: var(--rule-bar) solid var(--color-rule-2); }
table.data th, table.data td { padding: var(--space-xs) var(--space-sm); border-bottom: var(--rule-hair) solid var(--color-rule); text-align: right; }
table.data td { font-family: var(--font-outlier); font-weight: 500; }
table.data td.left { font-family: var(--font-body); }
table.data th { color: var(--color-ink-muted); font-weight: 500; font-size: var(--text-xs); border-bottom: var(--rule-bar) solid var(--color-rule-2);
                white-space: normal; word-break: keep-all; line-height: 1.3; vertical-align: bottom; min-width: 3.2em; } /* 긴 머리글은 표를 넓히지 않고 줄바꿈 */
table.data tbody tr:last-child td { border-bottom: 0; }
table.data .left { text-align: left; }
table.data td.strong { font-weight: 700; color: var(--color-ink); }
table.data tbody tr { transition: background-color var(--dur-micro) var(--ease-out); }
table.data tbody tr:hover td { background: var(--color-paper-2); }
table.data .sticky { position: sticky; left: 0; background: var(--color-paper); z-index: var(--z-base); }
table.data th.sortable { cursor: pointer; user-select: none; }
table.data th.sortable button { all: unset; cursor: pointer; }
table.data th.sortable button:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
table.data th[aria-sort] { color: var(--color-ink); font-weight: 700; }
/* 순위 칸: 포스트시즌권(po)은 잉크로 채운 칸, 준플레이오프권(semi)은 옅게 채운 칸. 뜻은 표 아래 범례가 설명한다 */
table.data td.rank { font-weight: 700; text-align: center; }
/* 표 안의 링크(팀·선수 이름)와 기사 제목·태그는 밑줄 없이 두고, 올렸을 때만 밑줄을 보인다(행마다 밑줄이 그어지면 표가 지저분해진다) */
table.data a, .articles a { text-decoration-line: none; }
table.data a:hover { text-decoration-line: underline; }
table.data tr.po td.rank, table.data tr.po:hover td.rank { background: var(--color-ink); color: var(--color-paper); }
table.data tr.semi td.rank, table.data tr.semi:hover td.rank { background: var(--color-paper-3); }
.zone-legend { display: flex; align-items: center; gap: var(--space-2xs) var(--space-xs); flex-wrap: wrap; margin: var(--space-xs) 0 0; }
.zone-legend .key { display: inline-block; width: 12px; height: 12px; background: var(--color-ink); }
.zone-legend .key.semi { background: var(--color-paper-3); border: var(--rule-hair) solid var(--color-rule); margin-left: var(--space-xs); }

/* 탭(토글 버튼 묶음): 선택된 탭은 라임 채움 + 잉크 글자 */
.tabs { display: flex; gap: var(--space-2xs); flex-wrap: wrap; margin-bottom: var(--space-sm); }
.tab { border: var(--rule-hair) solid var(--color-rule); background: var(--color-paper); color: var(--color-ink-muted); padding: 0 var(--space-sm);
       border-radius: var(--radius-card); cursor: pointer; font-size: var(--text-sm); font-weight: 500; min-height: 36px;
       transition: background-color var(--dur-micro) var(--ease-out), color var(--dur-micro) var(--ease-out), border-color var(--dur-micro) var(--ease-out); }
.tab:hover { border-color: var(--color-ink); color: var(--color-ink); }
.tab:active { background: var(--color-paper-3); }
.tab.active, .tab[aria-pressed='true'] { background: var(--color-volt); border-color: var(--color-ink); color: var(--color-volt-ink); font-weight: 700; }
.tabs.has-glider > .glider { background: var(--color-volt); border-radius: var(--radius-card); }
select { padding: 0 var(--space-sm); min-height: 36px; border: var(--rule-hair) solid var(--color-rule); border-radius: var(--radius-card);
         background: var(--color-paper); color: var(--color-ink); cursor: pointer; }
select:hover { border-color: var(--color-ink); }
@media (pointer: coarse) { .tab, select { min-height: 44px; } }

/* 배지: 승 = 잉크 채움, 패 = 옅은 칸, 오늘 = 라임 채움 */
.badge { display: inline-block; padding: 0 var(--space-xs); border-radius: 2px; font-size: var(--text-xs); font-weight: 700; line-height: 1.7; }
.badge.w { background: var(--color-ink); color: var(--color-paper); }
.badge.l { background: var(--color-paper-3); color: var(--color-ink-muted); }
.badge.today { background: var(--color-volt); color: var(--color-volt-ink); }
.empty { color: var(--color-ink-muted); padding: var(--space-lg) 0; text-align: center; }

/* 버튼: 기본은 잉크 테두리, 주 버튼은 라임 채움. 글자는 표제 글꼴 */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: var(--space-xs); min-height: 40px; padding: 0 var(--space-md);
       border: var(--rule-bar) solid var(--color-ink); border-radius: var(--radius-card); background: var(--color-paper); color: var(--color-ink);
       font-family: var(--font-display); font-weight: var(--display-weight); font-synthesis: none; font-size: var(--text-md);
       text-transform: uppercase; text-decoration: none; cursor: pointer;
       transition: background-color var(--dur-micro) var(--ease-out), color var(--dur-micro) var(--ease-out), border-color var(--dur-micro) var(--ease-out); }
.btn:hover { background: var(--color-ink); color: var(--color-paper); }
.btn:active { background: var(--color-ink-2); color: var(--color-paper); }
.btn.primary { background: var(--color-volt); border-color: var(--color-volt); color: var(--color-volt-ink); }
.btn.primary:hover { background: var(--color-ink); border-color: var(--color-ink); color: var(--color-paper); }
.btn.danger { color: var(--color-error); border-color: var(--color-error); }
.btn.danger:hover { background: var(--color-error); color: var(--color-paper); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.field { display: grid; gap: var(--space-2xs); margin-bottom: var(--space-md); }
.field > label { font-weight: 700; font-size: var(--text-sm); }
.field .hint { font-size: var(--text-xs); color: var(--color-ink-muted); margin: 0; }
.field .err { font-size: var(--text-sm); color: var(--color-error); margin: 0; }
.input { width: 100%; min-height: 44px; padding: var(--space-xs) var(--space-sm); color: var(--color-ink); background: var(--color-paper);
         border: var(--rule-hair) solid var(--color-rule); border-radius: var(--radius-card); transition: border-color var(--dur-micro) var(--ease-out); }
.input:hover { border-color: var(--color-ink); }
.input[aria-invalid='true'] { border-color: var(--color-error); }
@media (pointer: coarse) { .btn { min-height: 44px; } }
</style>
