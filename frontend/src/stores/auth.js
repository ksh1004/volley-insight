import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/client'

// 로그인 상태 저장소(Pinia): 로그인한 회원과 관심 팀·선수(WEB-11, WEB-12). 어느 화면에서든 같은 값을 쓴다.
// 토큰은 localStorage에 두어 새로 고침해도 로그인이 유지되고, 회원 정보와 관심 목록은 그 토큰으로 다시 받아 온다.
export const useAuthStore = defineStore('auth', () => {
  const token = ref(api.readToken())
  const user = ref(null)
  const favorites = ref({ teams: [], players: [] })
  const ready = ref(false) // 첫 /api/me 응답을 받았는가(토큰이 없었으면 바로 true)
  const isLoggedIn = computed(() => Boolean(token.value))

  // 로그인·가입·내 정보 응답을 상태에 반영(토큰이 있으면 저장)
  function apply(data) {
    if (data.token) {
      token.value = data.token
      api.writeToken(data.token)
    }
    user.value = data.user
    favorites.value = data.favorites
  }

  // 로그아웃 상태로(토큰 삭제). 서버가 토큰을 거절했을 때도 불린다
  function clear() {
    token.value = ''
    user.value = null
    favorites.value = { teams: [], players: [] }
    api.writeToken('')
  }
  api.onTokenExpired(clear)

  // 앱이 처음 화면을 그리기 전에 한 번 부른다(router.beforeEach). 여러 번 불려도 요청은 한 번만(loading 공유)
  let loading = null
  function init() {
    if (ready.value || !token.value) {
      ready.value = true
      return Promise.resolve()
    }
    loading ||= api.getMe().then(apply).catch(() => { /* 401이면 이미 토큰이 지워졌다 */ })
      .finally(() => { ready.value = true })
    return loading
  }

  const login = async (body) => apply(await api.login(body))
  const signup = async (body) => apply(await api.signup(body))

  async function logout() {
    try { await api.logout() } catch { /* 실패해도 어차피 브라우저의 토큰은 지운다 */ }
    clear()
  }

  async function deleteAccount(password) {
    await api.deleteMe(password)
    clear()
  }

  // 관심 등록 여부(kind: 'team' | 'player')
  const isFavorite = (kind, code) => favorites.value[`${kind}s`].some((x) => x.code === code)

  // 관심 추가/해제. 서버가 돌려준 최신 목록으로 바꾼다
  async function toggleFavorite(kind, code) {
    favorites.value = isFavorite(kind, code) ? await api.removeFavorite(kind, code) : await api.addFavorite(kind, code)
  }

  return { token, user, favorites, ready, isLoggedIn, init, login, signup, logout, deleteAccount, isFavorite, toggleFavorite }
})
