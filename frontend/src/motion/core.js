// 직접 만든 모션 도구 모음(애니메이션 라이브러리 없이). 시간·가속 곡선은 motion.css의 CSS 토큰과 같은 값이다.
// 규칙(design-system MASTER.md + ui-ux-pro-max 애니메이션 지침):
//   - 움직임은 데이터나 위치가 바뀐 것을 설명할 때만 쓴다(꾸미기용 움직임 없음)
//   - 들어오는 것은 감속하며 도착한다
//   - 로딩·실시간 표시 말고는 반복 애니메이션이 없다
//   - 운영체제의 '동작 줄이기' 설정이 켜져 있으면 최종 상태를 바로 보여 준다(접근성)
// 들어 있는 것: DURATION/ease(시간·곡선), reducedMotion(동작 줄이기 여부), useTween(숫자 부드럽게),
//              useInView(화면에 보였는가), useFlash(잠깐 켜졌다 꺼지는 표시)
import { onBeforeUnmount, onMounted, onScopeDispose, ref, unref, watch } from 'vue'

// 애니메이션 시간(ms): 작은 상태 변화 / 기본 / 큰 이동 / 숫자 세기 / 차트 그리기
export const DURATION = { fast: 160, base: 240, slow: 420, number: 600, chart: 750 }

// 가속 곡선: 0~1 진행률을 0~1 위치로 바꾸는 함수
export const ease = {
  out: (t) => 1 - (1 - t) ** 3, // 도착: 빠르게 출발해 부드럽게 멈춤
  outExpo: (t) => (t === 1 ? 1 : 1 - 2 ** (-10 * t)),
  inOut: (t) => (t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2), // 두 위치 사이 이동: 천천히 출발해 천천히 멈춤
  linear: (t) => t,
}

// ---------------------------------------------------------------- 동작 줄이기 설정(앱 전체가 감시자 하나를 공유)
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null
export const reducedMotion = ref(Boolean(media?.matches))
media?.addEventListener?.('change', (e) => { reducedMotion.value = e.matches })

// ---------------------------------------------------------------- tween
/**
 * `source`를 부드럽게 따라가는 숫자. 값이 바뀔 때마다 '지금 화면에 보이는 값'에서 출발하므로, 애니메이션 도중에
 * 새 값이 와도 튀지 않고 그 자리에서 이어 간다. requestAnimationFrame으로 화면 갱신마다 값을 조금씩 옮긴다.
 *   const shown = useTween(() => props.value)        // shown.value: 화면에 그릴 숫자
 *   shown.play(0)                                    // 0부터 지금 값까지 세어 올라가기(예: 화면에 보였을 때)
 */
export function useTween(source, { duration = DURATION.number, easing = ease.out, from } = {}) {
  const read = typeof source === 'function' ? source : () => unref(source)
  const out = ref(from ?? read())
  let raf = 0

  // start에서 target까지 dur 동안. 숫자가 아니거나 동작 줄이기면 바로 target으로
  function animate(start, target, dur) {
    cancelAnimationFrame(raf)
    const ok = Number.isFinite(start) && Number.isFinite(target)
    if (!ok || reducedMotion.value || dur <= 0 || start === target || typeof requestAnimationFrame === 'undefined') {
      out.value = target
      return
    }
    const t0 = performance.now()
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur)
      out.value = start + (target - start) * easing(p)
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
  }

  watch(read, (target) => animate(out.value, target, duration))
  onScopeDispose(() => cancelAnimationFrame(raf))
  out.play = (start = 0, dur = duration) => animate(start, read(), dur)
  return out
}

// ---------------------------------------------------------------- 화면에 보였는가
// ref가 컴포넌트를 가리키면 그 루트 요소를, 아니면 요소 그대로
const elementOf = (target) => {
  const v = unref(target)
  return v?.$el ?? v
}

/** 요소가 스크롤되어 화면에 들어오면 true(동작 줄이기거나 브라우저가 지원하지 않으면 바로 true).
 *  IntersectionObserver: 요소가 화면에 보이는지 브라우저가 알려 주는 기능. once면 한 번 보인 뒤 감시를 끝낸다. */
export function useInView(target, { once = true, threshold = 0.2, rootMargin = '0px 0px -8% 0px' } = {}) {
  const inView = ref(false)
  let io = null
  onMounted(() => {
    const el = elementOf(target)
    if (!el || reducedMotion.value || typeof IntersectionObserver === 'undefined') {
      inView.value = true
      return
    }
    io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          inView.value = true
          if (once) io.disconnect()
        } else if (!once) inView.value = false
      }
    }, { threshold, rootMargin })
    io.observe(el)
  })
  onBeforeUnmount(() => io?.disconnect())
  return inView
}

// ---------------------------------------------------------------- 한 번 켜졌다 꺼지는 표시(다시 켤 수 있는 CSS 애니메이션)
/** pulse(값)이 `flash`를 { value, n }으로 바꾼다. n이 매번 달라지므로 :key로 쓰면 CSS 애니메이션이 매번 처음부터 다시 돈다.
 *  ms 뒤 자동으로 null. 예: 실시간 화면에서 득점한 팀 점수를 잠깐 빛나게. */
export function useFlash(ms = 900) {
  const flash = ref(null)
  let timer = 0
  let n = 0
  function pulse(value) {
    if (reducedMotion.value) return
    clearTimeout(timer)
    n += 1
    flash.value = { value, n }
    timer = setTimeout(() => { flash.value = null }, ms)
  }
  onScopeDispose(() => clearTimeout(timer))
  return { flash, pulse }
}
