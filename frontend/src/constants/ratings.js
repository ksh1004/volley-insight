// 포지션별 능력치(선수 카드, spark_jobs/player_ratings.py)를 화면에 보여 줄 때 쓰는 값들:
// 포지션 순서·이름, 능력치마다 무엇을 쟀는지 설명, 점수 색 구간, 근거 기록 문구, 시즌 연도 표기.
// 눈금: 60 = 같은 성별·포지션 순위 자격 선수의 평균, 표준편차 1당 12점(1~99).
// 종합은 능력치 점수의 가중 평균이라 흩어짐이 더 작다(표준편차 1당 약 8점).

// 포지션 탭 순서
export const POSITION_ORDER = ['OH', 'OP', 'MB', 'S', 'L']
export const POSITION_NAMES = { OH: '아웃사이드 히터', OP: '아포짓', MB: '미들 블로커', S: '세터', L: '리베로' }

// 능력치 키 → 무엇을 쟀는지(카드에서 항목을 가리키거나 표의 칸에 마우스를 올리면 보이는 설명)
export const ABILITY_HELP = {
  receive: '서브를 받아 세터에게 정확히 올린 비율이에요.',
  scoring: '세트당 득점이에요. 팀이 공을 믿고 맡기는 정도도 함께 반영돼요.',
  block: '세트당 블로킹 득점이에요.',
  serve: '에이스이거나 상대 리시브를 흔든 서브의 비율이에요(2023-24 이전 시즌은 에이스 기록으로 계산).',
  attack: '공격 효율: (성공 - 차단 - 범실) / 시도. 세터·리시브 영향이 있어 시도가 적으면 평균 쪽으로 크게 당겨요.',
  defense: '세트당 디그예요.',
  touch: '블로킹 시도 중 손에 맞혀 우리 수비로 이어 준 비율(유효 블로킹)이에요.',
  quick: '속공·이동 공격 성공률이에요. 세터와 리시브의 영향도 받아요.',
  setting: '같은 공격수가 다른 세터의 공을 받았을 때보다, 이 세터의 공을 받았을 때 얼마나 더 득점했는지예요(2023-24부터). 그 전 시즌은 세트 성공률을 참고로 써요.',
  dig: '세트당 디그와 디그 성공률을 함께 봐요.',
  linking: '두 번째 볼(토스)을 얼마나 맡는지예요. 질은 기록으로 알 수 없어 종합에는 넣지 않아요.',
}

// 점수 구간(막대 색을 정하는 데만 쓴다. 숫자는 항상 옆에 함께 적으므로 색만으로 뜻을 전하지 않는다)
export const bandOf = (score) => (score == null ? 'none' : score >= 80 ? 'top' : score >= 70 ? 'high' : score >= 60 ? 'mid' : 'low')

// 근거 기록 한 줄: "리시브 정확 52.1%". 통산 카드는 그 숫자가 경력의 일부 기간만 해당하면 기간을 덧붙인다
export function formatStat(stat) {
  const v = stat.value
  const when = stat.period ? ` (${stat.period})` : ''
  if (v == null) return `${stat.label} -${when}`
  if (stat.unit === 'pct') return `${stat.label} ${(v * 100).toFixed(1)}%${when}`
  if (stat.unit === 'pp') return `${stat.label} ${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%p${when}`
  return `${stat.label} ${v.toFixed(2)}${when}`
}

// 시즌 연도 표기: "2025-2026" → "2025–2026"(긴 형태) 또는 "25–26"(짧은 형태, 능력치 카드의 시즌 띠용)
export const yearsLong = (y) => (y || '').replace('-', '–')
export const yearsShort = (y) => (y || '').replace(/^\d{2}(\d{2})-\d{2}(\d{2})$/, '$1–$2')
