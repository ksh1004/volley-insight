import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import { useAuthStore } from '@/stores/auth'

// Screens from design.md 1.10. Detail pages are loaded on demand.
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
    props: (route) => ({ matchKey: route.params.key }), // `key` is reserved in Vue, so pass it under another name
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
  scrollBehavior: (to, from, saved) => saved || { top: 0 },
})

// only same-site paths are followed after login (no open redirect)
export const safeNext = (next) => (typeof next === 'string' && /^\/(?![/\\])/.test(next) ? next : '/')

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.requiresAuth && !auth.isLoggedIn) return { name: 'login', query: { next: to.fullPath } }
  if (to.meta.guestOnly && auth.isLoggedIn) return safeNext(to.query.next)
  return true
})

export default router
