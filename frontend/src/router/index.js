import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

// Screens from design.md 1.10 are added here as they are implemented.
const routes = [{ path: '/', name: 'home', component: HomeView }]

export default createRouter({ history: createWebHistory(), routes })
