import { defineStore } from 'pinia'
import { getSeasons } from '@/api/client'

// Season and gender chosen by the viewer, shared by every page and remembered in this browser.
const KEY = 'volley.filters'

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {} } catch { return {} }
}

export const useFiltersStore = defineStore('filters', {
  state: () => ({ seasons: [], season: load().season || '', gender: load().gender || 'M', loaded: false }),
  getters: {
    current: (s) => s.seasons.find((x) => x.code === s.season) || s.seasons[0] || null,
  },
  actions: {
    async init() {
      if (this.loaded) return
      this.seasons = await getSeasons()
      if (!this.seasons.some((s) => s.code === this.season)) this.season = this.seasons[0]?.code || ''
      this.loaded = true
    },
    set(changes) {
      Object.assign(this, changes)
      try { localStorage.setItem(KEY, JSON.stringify({ season: this.season, gender: this.gender })) } catch { /* ignore */ }
    },
  },
})
