// One match's live state over Server-Sent Events (WEB-04, design.md 4.3).
// The gateway sends `snapshot` (state + every point so far) on connect, then an `update` per Flink message.
// EventSource reconnects by itself; each reconnect starts with a fresh snapshot, so nothing is missed.
import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { liveStreamUrl } from '@/api/client'

export function useLiveStream(key) {
  const match = shallowRef(null)
  const state = shallowRef(null)
  const points = shallowRef([]) // sorted by set, point_no
  const connection = ref('connecting') // connecting | open | reconnecting | closed | unsupported
  const error = ref(null)
  const lastAt = ref(null) // when the newest data was collected (ISO)
  let source = null

  function setPoints(list) {
    points.value = [...list].sort((a, b) => a.set - b.set || a.point_no - b.point_no)
  }

  function mergePoints(list) {
    const byKey = new Map(points.value.map((p) => [`${p.set}-${p.point_no}`, p]))
    for (const p of list) byKey.set(`${p.set}-${p.point_no}`, p)
    setPoints([...byKey.values()])
  }

  function onSnapshot(e) {
    const data = JSON.parse(e.data)
    match.value = data.match
    state.value = data.state
    setPoints(data.points || [])
    lastAt.value = data.state?.fetched_at || data.state?.updated_at || null
    connection.value = 'open'
  }

  function onUpdate(e) {
    const msg = JSON.parse(e.data)
    const s = msg.state
    // a new replay of the same match starts over; an older message (reordered) is ignored
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

  function close() {
    if (source) source.close()
    source = null
  }

  open()
  onBeforeUnmount(close)
  return { match, state, points, connection, error, lastAt, close }
}
