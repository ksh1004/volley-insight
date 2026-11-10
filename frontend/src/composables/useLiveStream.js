// 경기 하나의 실시간 상태를 SSE(Server-Sent Events)로 받는 컴포저블(WEB-04, 설계서 4.3). 실시간 화면이 쓴다.
// 게이트웨이는 접속하면 `snapshot`(상태 + 지금까지의 모든 득점)을 한 번 보내고, 이후 Flink 메시지마다 `update`를 보낸다.
// EventSource는 끊기면 알아서 다시 연결하고, 다시 연결할 때마다 새 스냅숏부터 시작하므로 빠지는 것이 없다.
// 돌려주는 값은 모두 반응형(ref)이라 화면이 자동으로 다시 그려진다. 화면을 떠나면 연결을 닫는다.
import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { liveStreamUrl } from '@/api/client'

export function useLiveStream(key) {
  const match = shallowRef(null)
  const state = shallowRef(null)
  const points = shallowRef([]) // 득점 목록(세트, 득점 번호 순). shallowRef: 배열을 통째로 바꿔 갱신(깊은 감시 비용 절약)
  const connection = ref('connecting') // 연결 상태: connecting | open | reconnecting | closed | unsupported
  const error = ref(null)
  const lastAt = ref(null) // 가장 새 데이터를 수집한 시각(ISO). 화면에 '몇 초 전 갱신'으로 보여 줌
  let source = null

  // 득점 목록을 정렬해 통째로 바꾼다
  function setPoints(list) {
    points.value = [...list].sort((a, b) => a.set - b.set || a.point_no - b.point_no)
  }

  // 새 득점들을 합친다(같은 세트·득점 번호면 새 것으로 바꿈: 원인 선수가 나중에 채워지는 경우)
  function mergePoints(list) {
    const byKey = new Map(points.value.map((p) => [`${p.set}-${p.point_no}`, p]))
    for (const p of list) byKey.set(`${p.set}-${p.point_no}`, p)
    setPoints([...byKey.values()])
  }

  // 접속 직후의 전체 상태
  function onSnapshot(e) {
    const data = JSON.parse(e.data)
    match.value = data.match
    state.value = data.state
    setPoints(data.points || [])
    lastAt.value = data.state?.fetched_at || data.state?.updated_at || null
    connection.value = 'open'
  }

  // 이후의 갱신 하나
  function onUpdate(e) {
    const msg = JSON.parse(e.data)
    const s = msg.state
    // 같은 경기의 새 재생이면 처음부터, 순서가 뒤바뀌어 온 옛 메시지는 무시
    if (msg.reset || !state.value || s.session !== state.value.session) {
      setPoints(msg.points || [])
    } else if (s.last_event_seq < state.value.last_event_seq) {
      return
    } else {
      mergePoints(msg.points || [])
    }
    state.value = s
    lastAt.value = msg.fetched_at || msg.stored_at
  }

  // 연결 열기. 아주 오래된 브라우저처럼 EventSource가 없으면 'unsupported'
  function open() {
    if (typeof EventSource === 'undefined') {
      connection.value = 'unsupported'
      return
    }
    source = new EventSource(liveStreamUrl(key))
    source.addEventListener('snapshot', onSnapshot)
    source.addEventListener('update', onUpdate)
    source.onopen = () => { error.value = null }
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        connection.value = 'closed'
        error.value = '실시간 연결이 끊겼습니다.'
      } else {
        connection.value = 'reconnecting'
      }
    }
  }

  // 연결 닫기
  function close() {
    if (source) source.close()
    source = null
  }

  open()
  onBeforeUnmount(close)
  return { match, state, points, connection, error, lastAt, close }
}
