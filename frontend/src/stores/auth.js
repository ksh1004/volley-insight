import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/client'

// Signed-in user and their favourite teams/players (WEB-11, WEB-12). The token lives in localStorage so a
// reload keeps the login; the user and favourites are fetched again with it.
export const useAuthStore = defineStore('auth', () => {
  const token = ref(api.readToken())
  const user = ref(null)
  const favorites = ref({ teams: [], players: [] })
  const ready = ref(false) // first /api/me answered (or there was no token)
  const isLoggedIn = computed(() => Boolean(token.value))

  function apply(data) {
    if (data.token) {
      token.value = data.token
      api.writeToken(data.token)
    }
    user.value = data.user
    favorites.value = data.favorites
  }

  function clear() {
    token.value = ''
    user.value = null
    favorites.value = { teams: [], players: [] }
    api.writeToken('')
  }
  api.onTokenExpired(clear)

  let loading = null
  function init() {
    if (ready.value || !token.value) {
      ready.value = true
      return Promise.resolve()
    }
    loading ||= api.getMe().then(apply).catch(() => { /* 401 already cleared the token */ })
      .finally(() => { ready.value = true })
    return loading
  }

  const login = async (body) => apply(await api.login(body))
  const signup = async (body) => apply(await api.signup(body))

  async function logout() {
    try { await api.logout() } catch { /* the token is dropped either way */ }
    clear()
  }

  async function deleteAccount(password) {
    await api.deleteMe(password)
    clear()
  }

  const isFavorite = (kind, code) => favorites.value[`${kind}s`].some((x) => x.code === code)

  async function toggleFavorite(kind, code) {
    favorites.value = isFavorite(kind, code) ? await api.removeFavorite(kind, code) : await api.addFavorite(kind, code)
  }

  return { token, user, favorites, ready, isLoggedIn, init, login, signup, logout, deleteAccount, isFavorite, toggleFavorite }
})
