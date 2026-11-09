import axios from 'axios'

// All requests go to /api; the Vite dev server (and Nginx in production) forwards them to Django.
const client = axios.create({ baseURL: '/api', timeout: 15000 })

export const TOKEN_KEY = 'token'
export function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' } // storage blocked
}
export function writeToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* storage blocked: the login lasts until the tab closes */ }
}

// listeners told when the server rejects the saved token (signed out elsewhere, account deleted)
const expiredListeners = new Set()
export const onTokenExpired = (fn) => expiredListeners.add(fn)

client.interceptors.request.use((config) => {
  const token = config.anonymous ? '' : readToken()
  if (token) config.headers.Authorization = `Token ${token}`
  return config
})

// An unknown token makes Django answer 401 even on public pages, so drop it and retry the request once without it.
client.interceptors.response.use(undefined, (error) => {
  const cfg = error.config
  if (error.response?.status === 401 && cfg && !cfg.anonymous && cfg.headers?.Authorization) {
    writeToken('')
    expiredListeners.forEach((fn) => fn())
    const headers = { ...cfg.headers }
    delete headers.Authorization
    return client({ ...cfg, anonymous: true, headers })
  }
  return Promise.reject(error)
})

export default client

const get = (url, params) => client.get(url, { params }).then((r) => r.data)
// drop empty filters so URLs stay clean
const clean = (params) => Object.fromEntries(Object.entries(params || {}).filter(([, v]) => v !== '' && v != null))

export const getHealth = () => get('/health')
export const getSeasons = () => get('/seasons')
export const getTeams = (params) => get('/teams', clean(params))
export const getMatches = (params) => get('/matches', clean(params))
export const getMatch = (key) => get(`/matches/${key}`)
export const getRallies = (key, set) => get(`/matches/${key}/rallies`, { set })
export const getStandings = (params) => get('/standings', clean(params))
export const getPlayerRanking = (params) => get('/standings/players', clean(params))
export const getTeam = (code, season) => get(`/teams/${code}`, clean({ season }))
export const getPlayer = (code, season) => get(`/players/${code}`, clean({ season }))
export const getArticles = (params) => get('/articles', clean(params))
export const searchAll = (q, page) => get('/search', clean({ q, page }))
export const suggest = (q, signal) => client.get('/search/suggest', { params: { q }, signal }).then((r) => r.data)
export const getPredictionCheck = (gender) => get('/predictions', clean({ gender }))
export const getRatings = (params) => get('/ratings', clean(params))
export const getLive = () => get('/live')
export const getLiveMatch = (key) => get(`/live/${key}`)
// Server-Sent Events URL (EventSource cannot use axios; same origin, proxied to the SSE gateway)
export const liveStreamUrl = (key) => `/api/live/${key}/stream`

// accounts (WEB-11, WEB-12)
export const signup = (body) => client.post('/auth/signup', body).then((r) => r.data)
export const login = (body) => client.post('/auth/login', body).then((r) => r.data)
export const logout = () => client.post('/auth/logout')
export const getMe = () => get('/me')
export const deleteMe = (password) => client.delete('/me', { data: { password } })
export const getFeed = () => get('/me/feed')
export const addFavorite = (kind, code) => client.post('/me/favorites', { kind, code }).then((r) => r.data)
export const removeFavorite = (kind, code) => client.delete(`/me/favorites/${kind}/${code}`).then((r) => r.data)
