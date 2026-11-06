<script setup>
// WEB-12: follow a team or player. Signed out, it leads to login and comes back to this page.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({
  kind: { type: String, required: true }, // team | player
  code: { type: String, required: true },
  name: { type: String, default: '' },
})
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const busy = ref(false)
const message = ref('') // read out by screen readers
const error = ref('')

const on = computed(() => auth.isFavorite(props.kind, props.code))
const noun = computed(() => (props.kind === 'team' ? '관심 팀' : '관심 선수'))

async function toggle() {
  if (!auth.isLoggedIn) {
    router.push({ path: '/login', query: { next: route.fullPath } })
    return
  }
  busy.value = true
  message.value = ''
  error.value = ''
  const was = on.value
  try {
    await auth.toggleFavorite(props.kind, props.code)
    message.value = `${props.name} ${was ? `${noun.value}에서 뺐습니다` : `${noun.value}${props.kind === 'team' ? '으로' : '로'} 추가했습니다`}`
  } catch (e) {
    error.value = e.response?.data?.detail || '저장하지 못했습니다. 다시 시도해 주세요.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <span class="fav-wrap">
    <button type="button" class="fav" :class="{ on }" :aria-pressed="on" :disabled="busy"
            :title="auth.isLoggedIn ? '' : '로그인하면 관심 목록에 추가할 수 있습니다'" @click="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
      </svg>
      <span>{{ noun }}</span>
    </button>
    <span class="sr-only" role="status">{{ message }}</span>
    <span v-if="error" class="err small" role="alert">{{ error }}</span>
  </span>
</template>

<style scoped>
.fav-wrap { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fav { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 0 12px 0 10px; border-radius: 999px;
       border: 1px solid var(--color-border); background: var(--color-card); color: var(--color-muted-foreground);
       font: inherit; font-size: .88rem; font-weight: 600; cursor: pointer;
       transition: background-color .15s var(--ease), color .15s var(--ease), border-color .15s var(--ease); }
.fav:hover { border-color: var(--color-accent); color: var(--color-accent); }
.fav svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linejoin: round;
           transition: fill .15s var(--ease); }
.fav.on { background: var(--color-accent-soft); border-color: var(--color-accent); color: var(--color-accent); }
.fav.on svg { fill: currentColor; }
.fav:disabled { opacity: .7; cursor: progress; }
.err { color: var(--color-bad); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (pointer: coarse) { .fav { min-height: 44px; } }
</style>
