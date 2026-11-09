// Position ratings (player cards, spark_jobs/player_ratings.py): what each ability measures and how numbers read.
// Scale: 60 = average qualified player of the same gender and position, 12 points per standard deviation (1-99);
// the overall is the weighted average of the ability scores (spread about 8 points per standard deviation).

export const POSITION_ORDER = ['OH', 'OP', 'MB', 'S', 'L']
export const POSITION_NAMES = { OH: '아웃사이드 히터', OP: '아포짓', MB: '미들 블로커', S: '세터', L: '리베로' }

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

// ability score band (for the bar colour; the number is always written next to it)
export const bandOf = (score) => (score == null ? 'none' : score >= 80 ? 'top' : score >= 70 ? 'high' : score >= 60 ? 'mid' : 'low')

// "리시브 정확 52.1%"; career cards add the period when the number covers only part of the career
export function formatStat(stat) {
  const v = stat.value
  const when = stat.period ? ` (${stat.period})` : ''
  if (v == null) return `${stat.label} -${when}`
  if (stat.unit === 'pct') return `${stat.label} ${(v * 100).toFixed(1)}%${when}`
  if (stat.unit === 'pp') return `${stat.label} ${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%p${when}`
  return `${stat.label} ${v.toFixed(2)}${when}`
}

// "2025-2026" -> "2025–2026" (long) or "25–26" (short, for the season strip)
export const yearsLong = (y) => (y || '').replace('-', '–')
export const yearsShort = (y) => (y || '').replace(/^\d{2}(\d{2})-\d{2}(\d{2})$/, '$1–$2')
