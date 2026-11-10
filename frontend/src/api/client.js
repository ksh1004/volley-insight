// 백엔드(Django REST API) 호출을 한곳에 모은 모듈. 화면 코드는 여기 함수만 부른다(주소가 바뀌어도 여기만 고치면 됨).
// 로그인 토큰 보관, 요청마다 토큰 붙이기, 만료된 토큰 처리도 여기서 한다.
import axios from 'axios'

// 모든 요청은 /api로 보낸다. Vite 개발 서버(운영에서는 Nginx)가 이것을 Django로 전달한다(같은 주소라 CORS 불필요).
const client = axios.create({ baseURL: '/api', timeout: 15000 })

const TOKEN_KEY = 'token' // 브라우저 localStorage에 토큰을 저장하는 이름
// 저장된 로그인 토큰(없으면 ''). 브라우저가 저장소를 막아 두면(사생활 보호 모드 등) 예외가 나므로 감싼다
export function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' } // 저장소가 막힌 경우
}
// 토큰 저장(빈 값이면 삭제)
export function writeToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* 저장소가 막힘: 탭을 닫을 때까지만 로그인이 유지된다 */ }
}

// 서버가 저장된 토큰을 거절했을 때(다른 곳에서 로그아웃, 탈퇴 등) 알림을 받을 함수들(stores/auth.js가 등록)
const expiredListeners = new Set()
export const onTokenExpired = (fn) => expiredListeners.add(fn)

// 요청 가로채기: 토큰이 있으면 Authorization 머리글에 붙인다(anonymous 표시가 있는 재시도 요청은 제외)
client.interceptors.request.use((config) => {
  const token = config.anonymous ? '' : readToken()
  if (token) config.headers.Authorization = `Token ${token}`
  return config
})

// 응답 가로채기: 모르는 토큰이면 Django는 공개 화면 요청에도 401을 준다. 그래서 토큰을 버리고 토큰 없이 한 번만 다시 요청한다.
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

// GET 요청 후 응답 본문(JSON)만 돌려주는 짧은 도우미
const get = (url, params) => client.get(url, { params }).then((r) => r.data)
// 빈 필터 값은 빼서 주소를 깔끔하게(예: ?season=&gender=M → ?gender=M)
const clean = (params) => Object.fromEntries(Object.entries(params || {}).filter(([, v]) => v !== '' && v != null))

// ---- 조회 API (각 함수 이름이 곧 화면에서 쓰는 데이터. 응답 모양은 backend의 같은 이름 뷰 참고)
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
// 자동완성: signal로 이전 요청을 취소할 수 있게 한다(글자를 빨리 칠 때 늦게 온 옛 응답이 덮어쓰지 않도록)
export const suggest = (q, signal) => client.get('/search/suggest', { params: { q }, signal }).then((r) => r.data)
export const getPredictionCheck = (gender) => get('/predictions', clean({ gender }))
export const getRatings = (params) => get('/ratings', clean(params))
export const getLive = () => get('/live')
// 실시간 SSE 주소(EventSource는 axios를 쓸 수 없어 주소만 만든다. 같은 출처이고 SSE 게이트웨이로 전달된다)
export const liveStreamUrl = (key) => `/api/live/${key}/stream`

// ---- 회원 (WEB-11, WEB-12)
export const signup = (body) => client.post('/auth/signup', body).then((r) => r.data)
export const login = (body) => client.post('/auth/login', body).then((r) => r.data)
export const logout = () => client.post('/auth/logout')
export const getMe = () => get('/me')
export const deleteMe = (password) => client.delete('/me', { data: { password } })
export const getFeed = () => get('/me/feed')
export const addFavorite = (kind, code) => client.post('/me/favorites', { kind, code }).then((r) => r.data)
export const removeFavorite = (kind, code) => client.delete(`/me/favorites/${kind}/${code}`).then((r) => r.data)
