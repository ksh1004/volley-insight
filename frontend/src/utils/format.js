// Dates come from the API in KST with offset (e.g. 2025-10-20T19:00:00+09:00); show them in KST.
const TZ = 'Asia/Seoul'
const parts = (iso, opts) => new Intl.DateTimeFormat('ko-KR', { timeZone: TZ, ...opts }).format(new Date(iso))

export const fmtDate = (iso) => parts(iso, { month: 'long', day: 'numeric', weekday: 'short' })
export const fmtFullDate = (iso) => parts(iso, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' })
export const fmtTime = (iso) => parts(iso, { hour: '2-digit', minute: '2-digit', hour12: false })
export const dayKey = (iso) => parts(iso, { year: 'numeric', month: '2-digit', day: '2-digit' }) // stable grouping key
export const todayKst = () => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date()) // YYYY-MM-DD

export const num = (v, digits = 0) => (v == null ? '-' : Number(v).toLocaleString('ko-KR', {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
}))
export const pct = (v) => (v == null ? '-' : `${num(v, 1)}%`)
export const ratio = (v) => (v == null ? 'MAX' : num(v, 3))

// months of a season, e.g. ['2025-10', ..., '2026-04']
export function seasonMonths(start, end) {
  if (!start || !end) return []
  const out = []
  let [y, m] = start.split('-').map(Number)
  const [ey, em] = end.split('-').map(Number)
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  return out
}

// V-League postseason places: 1st-3rd qualify; 4th joins a semi play-off when within 3 points of 3rd.
export function postseasonZone(rows) {
  const zone = {}
  // only once every team has played: at the start of a season unplayed teams tie for 3rd
  if (!rows?.length || rows.some((r) => !r.games)) return zone
  const third = rows.find((r) => r.rank === 3) || rows[2]
  for (const r of rows) {
    if (r.rank <= 3) zone[r.team.code] = 'po'
    else if (r.rank === 4 && third && third.points - r.points <= 3) zone[r.team.code] = 'semi'
  }
  return zone
}

// "방금", "12분 전", "3시간 전", then the date (articles)
export function fmtAgo(iso, now = Date.now()) {
  if (!iso) return ''
  const min = Math.floor((now - new Date(iso).getTime()) / 60000)
  if (min < 1) return '방금'
  if (min < 60) return `${min}분 전`
  if (min < 24 * 60) return `${Math.floor(min / 60)}시간 전`
  return parts(iso, { month: 'long', day: 'numeric' })
}
