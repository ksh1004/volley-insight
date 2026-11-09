// In-house motion toolkit (no animation library). Durations and easings match the CSS tokens in motion.css.
// Rules (design-system MASTER.md + ui-ux-pro-max animation guidance): motion explains a change of data or place,
// entering things decelerate, nothing loops except loading/live indicators, and "reduce motion" shows the final
// state at once.
import { onBeforeUnmount, onMounted, onScopeDispose, ref, unref, watch } from 'vue'

export const DURATION = { fast: 160, base: 240, slow: 420, number: 600, chart: 750 }

export const ease = {
  out: (t) => 1 - (1 - t) ** 3, // arriving: fast start, soft landing
  outExpo: (t) => (t === 1 ? 1 : 1 - 2 ** (-10 * t)),
  inOut: (t) => (t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2), // moving between two places
  linear: (t) => t,
}

// ---------------------------------------------------------------- reduced motion (one shared listener)
const media = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null
export const reducedMotion = ref(Boolean(media?.matches))
media?.addEventListener?.('change', (e) => { reducedMotion.value = e.matches })

// ---------------------------------------------------------------- tween
/**
 * A number that follows `source` smoothly. Each change animates from the value shown now, so a change that
 * arrives mid-animation continues from where it is instead of jumping.
 *   const shown = useTween(() => props.value)        // shown.value: the number to draw
 *   shown.play(0)                                    // count up from 0 to the current value (e.g. once visible)
 */
export function useTween(source, { duration = DURATION.number, easing = ease.out, from } = {}) {
  const read = typeof source === 'function' ? source : () => unref(source)
  const out = ref(from ?? read())
  let raf = 0

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

// ---------------------------------------------------------------- in view
const elementOf = (target) => {
  const v = unref(target)
  return v?.$el ?? v
}

/** True once the element has scrolled into view (or at once when motion is reduced / unsupported). */
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

// ---------------------------------------------------------------- one-shot flag (re-triggerable CSS animation)
/** pulse(side) sets `flash` to { side, n } so a :key change restarts the CSS animation each time. */
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
