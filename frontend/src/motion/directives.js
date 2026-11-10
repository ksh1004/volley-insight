// 모션 도구의 Vue 지시자(directive)들. main.js에서 전역으로 등록한다(템플릿에서 v-이름으로 씀).
//   v-reveal / v-reveal="i"   요소가 처음 화면에 들어올 때 한 번 아래에서 떠오르며 나타남. i로 형제끼리 순서대로 시차
//   v-glider                  탭 묶음에서 선택된 탭(.tab[aria-pressed=true])으로 미끄러져 가는 강조 배경
//   v-glider:underline="sel"  선택된 링크로 미끄러지는 밑줄(머리글 메뉴), `sel` = 선택 표시 CSS 선택자
import { reducedMotion } from './core'

// ---------------------------------------------------------------- v-reveal
// 모든 v-reveal 요소가 감시자 하나를 함께 쓴다(요소마다 만들지 않아 가볍다)
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
    // 동작 줄이기거나 IntersectionObserver가 없으면 그냥 보이게 둔다: 절대 숨겨진 채로 남지 않게
    if (reducedMotion.value || typeof IntersectionObserver === 'undefined') return
    const i = Number.isFinite(binding.value) ? binding.value : 0
    el.style.setProperty('--reveal-delay', `${Math.min(i, 8) * 45}ms`) // 순서마다 45ms 시차(최대 8번째까지)
    el.classList.add('reveal')
    revealObserver().observe(el)
  },
  unmounted(el) {
    revealer?.unobserve(el)
  },
}

// ---------------------------------------------------------------- v-glider
// 기본으로 찾는 '선택된 탭' 선택자
const SELECTED = '.tab[aria-pressed="true"], .tab.active'

// 강조 배경(또는 밑줄)을 선택된 탭의 위치·크기로 옮긴다. 이동 자체는 CSS transition이 부드럽게 처리
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
    // 처음 놓을 때는 미끄러지지 않게 하고, 그다음부터 '선택 색은 글라이더가 보여 줌' 모드로 바꾼다
    g.ready = true
    requestAnimationFrame(() => el.classList.add('glider-ready'))
  }
}

export const glider = {
  // 묶음 맨 앞에 글라이더 요소를 넣고, 선택·크기 변화를 감시한다
  mounted(el, binding) {
    const node = document.createElement('span')
    node.className = `glider${binding.arg === 'underline' ? ' underline' : ''}`
    node.setAttribute('aria-hidden', 'true')
    el.classList.add('has-glider')
    el.prepend(node)
    el._glider = { node, selector: binding.value || SELECTED, underline: binding.arg === 'underline', ready: false }
    const update = () => requestAnimationFrame(() => place(el))
    // 선택 변화(aria-pressed, 라우터의 active 클래스)와 크기 변화(줄바꿈, 글꼴 로딩)가 생기면 글라이더를 다시 놓는다
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
