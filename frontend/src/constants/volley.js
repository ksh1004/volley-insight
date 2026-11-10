// KOVO 코드를 화면에 보여 줄 한국어 이름표(요구사항 부록 A). 문자중계 동작·결과 코드, 성별, 포지션, 경기 상태 등.
export const GENDERS = { M: '남자부', F: '여자부' }

export const POSITIONS = { OH: '아웃사이드 히터', OP: '아포짓', MB: '미들 블로커', S: '세터', L: '리베로' }

export const STATUS = { SCHEDULED: '예정', LIVE: '진행 중', FINISHED: '종료', CONFIRMED: '종료', CANCELLED: '미진행' }

// 문자중계 동작 코드(raction) → 이름
export const ACTIONS = {
  DF: '디그', RR: '리시브', SS: '세트', VV: '서브', VS: '스파이크 서브',
  AA: '오픈', AC: '퀵오픈', AT: '시간차', AE: '이동', AQ: '속공', AW: '후위 공격', AZ: '후위 공격',
  BB: '블로킹', BA: '블로킹 어시스트', BT: '유효 블로킹',
  TO: '팀 범실', OP: '상대 범실 득점',
  CO: '교체 아웃', CI: '교체 투입', CT: '작전 타임', CW: '경고', CY: '벌칙', CR: '세트 퇴장', CC: '경기 퇴장',
  ST: '세트 시작', ET: '세트 종료',
}

// 범실 결과 코드(er로 시작) → 범실 종류. 코드가 둘씩 있는 것은 KOVO가 시기에 따라 다른 코드를 쓴 것
const ERRORS = {
  er0: '범실', era: '범실', er1: '아웃', er3: '아웃', erb: '아웃', er2: '로테이션 범실', erc: '로테이션 범실',
  er4: '포히트', ere: '포히트', er5: '라인 오버', erf: '라인 오버', er6: '네트 터치', erg: '네트 터치',
  er7: '드리블', er8: '포지션 폴트', eri: '포지션 폴트', er9: '스크린', erj: '스크린', erd: '네트 걸림',
  erh: '더블 컨택트', erk: '캐치볼', erl: '오버 네트',
}
// 결과 코드(raresult) → 이름: 성공, 정확(리시브·세트), 실패, 차단됨 + 범실 종류들
export const RESULTS = { suc: '성공', exc: '정확', fal: '실패', tot: '', blk: '차단됨', ...ERRORS }

// 랠리에 속하지 않는 이벤트(교체, 작전 타임, 경고, 세트 시작·끝): 문자중계에서 작은 안내로 보여 준다
export const NON_PLAY = new Set(['CO', 'CI', 'CT', 'CW', 'CY', 'CR', 'CC', 'ST', 'ET'])

// 실시간 화면용(Flink live-metrics의 값): 실시간/재생, 득점 종류
export const LIVE_MODES = { LIVE: '실시간', REPLAY: '재생' }
export const POINT_KINDS = { ATTACK: '공격', BLOCK: '블로킹', ACE: '서브 에이스', OPP_ERROR: '상대 범실', OTHER: '득점' }
