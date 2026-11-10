import { defineStore } from 'pinia'
import { getSeasons } from '@/api/client'

// 보는 사람이 고른 시즌과 남녀부 필터 저장소. 모든 화면이 함께 쓰고, 이 브라우저에 기억해 둔다(다음 방문 때도 유지).
const KEY = 'volley.filters'

// 저장해 둔 필터(없거나 읽을 수 없으면 {})
function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {} } catch { return {} }
}

export const useFiltersStore = defineStore('filters', {
  state: () => ({ seasons: [], season: load().season || '', gender: load().gender || 'M', loaded: false }),
  getters: {
    // 지금 고른 시즌 객체(없으면 가장 최근 시즌)
    current: (s) => s.seasons.find((x) => x.code === s.season) || s.seasons[0] || null,
  },
  actions: {
    // 시즌 목록을 한 번 받아 오고, 저장된 시즌이 목록에 없으면 가장 최근 시즌으로
    async init() {
      if (this.loaded) return
      this.seasons = await getSeasons()
      if (!this.seasons.some((s) => s.code === this.season)) this.season = this.seasons[0]?.code || ''
      this.loaded = true
    },
    // 필터 변경 + 브라우저에 저장. 예: filters.set({ gender: 'F' })
    set(changes) {
      Object.assign(this, changes)
      try { localStorage.setItem(KEY, JSON.stringify({ season: this.season, gender: this.gender })) } catch { /* ignore */ }
    },
  },
})
