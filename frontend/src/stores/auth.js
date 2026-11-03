import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

// Holds the login token (WEB-11). Login/logout calls are added with the accounts API.
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const isLoggedIn = computed(() => Boolean(token.value))

  function setToken(value) {
    token.value = value
    if (value) localStorage.setItem('token', value)
    else localStorage.removeItem('token')
  }

  return { token, isLoggedIn, setToken }
})
