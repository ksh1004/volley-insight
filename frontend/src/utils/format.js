// 화면 표시용 형식 함수들(날짜, 숫자, 시즌 월, 포스트시즌 구간, '몇 분 전').
// 날짜는 API에서 시간대가 붙은 서울 시각으로 온다(예: 2025-10-20T19:00:00+09:00). 보는 사람의 PC 시간대와 상관없이 서울 시각으로 보여 준다.
const TZ = 'Asia/Seoul'
// ISO 시각을 서울 시각 기준으로 원하는 모양(opts)의 한국어 문자열로
const parts = (iso, opts) => new Intl.DateTimeFormat('ko-KR', { timeZone: TZ, ...opts }).format(new Date(iso))

// 예: "10월 20일 (월)"
export const fmtDate = (iso) => parts(iso, { month: 'long', day: 'numeric', weekday: 'short' })
// 예: "2025년 10월 20일 (월)"
export const fmtFullDate = (iso) => parts(iso, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' })
// 예: "19:00"
export const fmtTime = (iso) => parts(iso, { hour: '2-digit', minute: '2-digit', hour12: false })
export const dayKey = (iso) => parts(iso, { year: 'numeric', month: '2-digit', day: '2-digit' }) // 날짜별로 묶을 때 쓰는 키
export const todayKst = () => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date()) // 서울 기준 오늘 "YYYY-MM-DD"(en-CA 형식이 이 모양)

// 숫자를 천 단위 쉼표와 소수 자릿수로(값이 없으면 '-')
export const num = (v, digits = 0) => (v == null ? '-' : Number(v).toLocaleString('ko-KR', {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
}))
// 백분율 "52.1%"
export const pct = (v) => (v == null ? '-' : `${num(v, 1)}%`)
// 득실률(잃은 게 없어 null이면 'MAX')
export const ratio = (v) => (v == null ? 'MAX' : num(v, 3))

// 시즌 시작일~종료일 사이의 달 목록. 예: ['2025-10', ..., '2026-04'] (일정 화면의 달 선택)
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

// V-리그 포스트시즌 진출 구간: 1~3위 진출, 4위는 3위와 승점 3점 이내면 준플레이오프. 순위표의 색 띠에 쓴다.
export function postseasonZone(rows) {
  const zone = {}
  // 모든 팀이 한 경기 이상 치른 뒤에만 표시: 시즌 초에는 안 치른 팀들이 공동 3위가 되어 의미가 없다
  if (!rows?.length || rows.some((r) => !r.games)) return zone
  const third = rows.find((r) => r.rank === 3) || rows[2]
  for (const r of rows) {
    if (r.rank <= 3) zone[r.team.code] = 'po'
    else if (r.rank === 4 && third && third.points - r.points <= 3) zone[r.team.code] = 'semi'
  }
  return zone
}

// 기사 시각 표시: "방금", "12분 전", "3시간 전", 하루가 넘으면 날짜
export function fmtAgo(iso, now = Date.now()) {
  if (!iso) return ''
  const min = Math.floor((now - new Date(iso).getTime()) / 60000)
  if (min < 1) return '방금'
  if (min < 60) return `${min}분 전`
  if (min < 24 * 60) return `${Math.floor(min / 60)}시간 전`
  return parts(iso, { month: 'long', day: 'numeric' })
}
