import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

// Screens from design.md 1.10. Detail pages are loaded on demand.
const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/schedule', name: 'schedule', component: () => import('@/views/ScheduleView.vue') },
  { path: '/standings', name: 'standings', component: () => import('@/views/StandingsView.vue') },
  {
    path: '/matches/:key(\\d{3}-\\d+)',
    name: 'match',
    component: () => import('@/views/MatchDetailView.vue'),
    props: (route) => ({ matchKey: route.params.key }), // `key` is reserved in Vue, so pass it under another name
  },
  { path: '/teams/:code', name: 'team', component: () => import('@/views/TeamView.vue'), props: true },
  { path: '/players/:code', name: 'player', component: () => import('@/views/PlayerView.vue'), props: true },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (to, from, saved) => saved || { top: 0 },
})
