// 라우터: 주소(URL)와 화면(View 컴포넌트)의 대응표, 그리고 로그인이 필요한 화면을 막는 검사.
import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import { useAuthStore } from '@/stores/auth'

// 설계서 1.10의 화면들. 홈만 처음부터 불러오고, 나머지는 그 화면에 처음 갈 때 불러온다(() => import(...): 첫 화면을 빨리 띄우기 위해).
// meta.requiresAuth: 로그인해야 볼 수 있음, meta.guestOnly: 로그인한 사람은 볼 필요 없음(로그인·가입 화면)
const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/schedule', name: 'schedule', component: () => import('@/views/ScheduleView.vue') },
  { path: '/standings', name: 'standings', component: () => import('@/views/StandingsView.vue') },
  { path: '/news', name: 'news', component: () => import('@/views/NewsView.vue') },
  { path: '/predictions', name: 'predictions', component: () => import('@/views/PredictionsView.vue') },
  { path: '/ratings', name: 'ratings', component: () => import('@/views/RatingsView.vue') },
  { path: '/search', name: 'search', component: () => import('@/views/SearchView.vue') },
  { path: '/login', name: 'login', component: () => import('@/views/AuthView.vue'), props: { mode: 'login' }, meta: { guestOnly: true } },
  { path: '/signup', name: 'signup', component: () => import('@/views/AuthView.vue'), props: { mode: 'signup' }, meta: { guestOnly: true } },
  { path: '/me', name: 'me', component: () => import('@/views/AccountView.vue'), meta: { requiresAuth: true } },
  {
    path: '/matches/:key(\\d{3}-\\d+)',
    name: 'match',
    component: () => import('@/views/MatchDetailView.vue'),
    props: (route) => ({ matchKey: route.params.key }), // Vue에서 `key`는 예약어라 다른 이름으로 넘긴다
  },
  {
    path: '/matches/:key(\\d{3}-\\d+)/live',
    name: 'match-live',
    component: () => import('@/views/MatchLiveView.vue'),
    props: (route) => ({ matchKey: route.params.key }),
  },
  { path: '/teams/:code', name: 'team', component: () => import('@/views/TeamView.vue'), props: true },
  { path: '/players/:code', name: 'player', component: () => import('@/views/PlayerView.vue'), props: true },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 뒤로 가기면 보던 위치로, 새 화면이면 맨 위로
  scrollBehavior: (to, from, saved) => saved || { top: 0 },
})

// 로그인 후 돌아갈 주소(?next=)는 이 사이트 안의 경로만 허용한다(다른 사이트로 보내는 '오픈 리다이렉트' 공격 방지)
export const safeNext = (next) => (typeof next === 'string' && /^\/(?![/\\])/.test(next) ? next : '/')

// 화면 이동마다 먼저 실행: 로그인 상태를 확인하고, 필요하면 로그인 화면으로 보내거나 원래 가려던 곳으로 돌려보낸다
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.requiresAuth && !auth.isLoggedIn) return { name: 'login', query: { next: to.fullPath } }
  if (to.meta.guestOnly && auth.isLoggedIn) return safeNext(to.query.next)
  return true
})

export default router
