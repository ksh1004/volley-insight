import axios from 'axios'

// All requests go to /api; the Vite dev server (and Nginx in production) forwards them to Django.
const client = axios.create({ baseURL: '/api', timeout: 15000 })

client.interceptors.request.use((config) => {
  let token = null
  try { token = localStorage.getItem('token') } catch { /* storage blocked */ }
  if (token) config.headers.Authorization = `Token ${token}`
  return config
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
