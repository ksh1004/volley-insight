// Directives of the motion toolkit, registered in main.js.
//   v-reveal / v-reveal="i"   fade-and-rise once when the element first scrolls into view; i staggers siblings
//   v-glider                  a highlight that slides to the selected tab of a group (pressed .tab buttons)
//   v-glider:underline="sel"  an underline that slides to the active link (header menu), `sel` = CSS selector
import { reducedMotion } from './core'

// ---------------------------------------------------------------- v-reveal
let revealer = null
function revealObserver() {
  revealer ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue
      e.target.classList.add('is-in')
      revealer.unobserve(e.target)
    }
  }, { threshold: 0.08, rootMargin: '0px 0px -6% 0px' })
  return revealer
}

export const reveal = {
  mounted(el, binding) {
    // without motion (or without IntersectionObserver) the element is simply there: never hidden
    if (reducedMotion.value || typeof IntersectionObserver === 'undefined') return
    const i = Number.isFinite(binding.value) ? binding.value : 0
    el.style.setProperty('--reveal-delay', `${Math.min(i, 8) * 45}ms`)
    el.classList.add('reveal')
    revealObserver().observe(el)
  },
  unmounted(el) {
    revealer?.unobserve(el)
  },
}

// ---------------------------------------------------------------- v-glider
const SELECTED = '.tab[aria-pressed="true"], .tab.active'

function place(el) {
  const g = el._glider
  if (!g) return
  const target = el.querySelector(g.selector)
  if (!target) {
    g.node.style.opacity = '0'
    return
  }
  const { offsetLeft: x, offsetTop: y, offsetWidth: w, offsetHeight: h } = target
  const s = g.node.style
  s.opacity = '1'
  s.width = `${w}px`
  if (g.underline) s.transform = `translate(${x}px, ${y + h - 2}px)`
  else {
    s.height = `${h}px`
    s.transform = `translate(${x}px, ${y}px)`
  }
  if (!g.ready) {
    // first placement without a slide, then switch the selected tab to "glider shows the colour"
    g.ready = true
    requestAnimationFrame(() => el.classList.add('glider-ready'))
  }
}

export const glider = {
  mounted(el, binding) {
    const node = document.createElement('span')
    node.className = `glider${binding.arg === 'underline' ? ' underline' : ''}`
    node.setAttribute('aria-hidden', 'true')
    el.classList.add('has-glider')
    el.prepend(node)
    el._glider = { node, selector: binding.value || SELECTED, underline: binding.arg === 'underline', ready: false }
    const update = () => requestAnimationFrame(() => place(el))
    // selection changes (aria-pressed / router active class) and size changes (wrapping, fonts) move the glider
    el._gliderMo = new MutationObserver(update)
    el._gliderMo.observe(el, { subtree: true, attributes: true, attributeFilter: ['aria-pressed', 'class'], childList: true })
    if (typeof ResizeObserver !== 'undefined') {
      el._gliderRo = new ResizeObserver(update)
      el._gliderRo.observe(el)
    }
    document.fonts?.ready?.then(update)
    update()
  },
  updated(el) {
    requestAnimationFrame(() => place(el))
  },
  unmounted(el) {
    el._gliderMo?.disconnect()
    el._gliderRo?.disconnect()
  },
}
