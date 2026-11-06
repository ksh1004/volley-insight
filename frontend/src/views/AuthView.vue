<script setup>
// WEB-11: email sign-up and login. Labels on every field, password managers and paste allowed, show/hide password,
// field errors under each field plus a focused summary at the top after a failed submit.
import { computed, nextTick, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { safeNext } from '@/router'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({ mode: { type: String, default: 'login' } })
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const isSignup = computed(() => props.mode === 'signup')
const form = reactive({ email: '', password: '', nickname: '' })
const errors = reactive({ email: '', password: '', nickname: '' })
const general = ref('')
const busy = ref(false)
const tried = ref(false) // the summary appears only after a submit; before that, errors show under each field
const showPw = ref(false)
const summary = ref(null)
const next = computed(() => safeNext(route.query.next))
const other = computed(() => ({ path: isSignup.value ? '/login' : '/signup', query: route.query.next ? { next: route.query.next } : {} }))

const LABELS = { email: '이메일', password: '비밀번호', nickname: '닉네임' }
const fields = computed(() => (isSignup.value ? ['email', 'nickname', 'password'] : ['email', 'password']))

function check(field) {
  const v = form[field].trim()
  let msg = ''
  if (field === 'email') {
    if (!v) msg = '이메일을 입력하세요.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) msg = '이메일 형식이 맞지 않습니다. 예: fan@example.com'
  } else if (field === 'password') {
    if (!form.password) msg = '비밀번호를 입력하세요.'
    else if (isSignup.value && form.password.length < 8) msg = '비밀번호는 8자 이상이어야 합니다.'
  } else if (field === 'nickname' && isSignup.value) {
    if (v.length < 2 || v.length > 20) msg = '닉네임은 2~20자로 입력하세요.'
  }
  errors[field] = msg
  return !msg
}
// re-check a field that already shows an error while the user fixes it
const recheck = (field) => { if (errors[field]) check(field) }

const errorList = computed(() => fields.value.filter((f) => errors[f]).map((f) => ({ field: f, msg: errors[f] })))

async function focusSummary() {
  await nextTick()
  summary.value?.focus()
}

async function submit() {
  general.value = ''
  tried.value = true
  const ok = fields.value.map(check).every(Boolean)
  if (!ok) return focusSummary()
  busy.value = true
  try {
    const body = { email: form.email.trim(), password: form.password }
    if (isSignup.value) await auth.signup({ ...body, nickname: form.nickname.trim() })
    else await auth.login(body)
    router.replace(next.value)
  } catch (e) {
    const data = e.response?.data || {}
    if (e.response?.status === 429) general.value = '시도가 너무 많습니다. 1분 뒤에 다시 해 주세요.'
    else if (data.detail) general.value = data.detail
    else if (e.response?.status === 400) {
      for (const f of fields.value) if (data[f]) errors[f] = [].concat(data[f]).join(' ')
      if (!errorList.value.length) general.value = '입력한 내용을 확인해 주세요.'
    } else general.value = '서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.'
    focusSummary()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="auth">
    <section class="card">
      <h1>{{ isSignup ? '회원가입' : '로그인' }}</h1>
      <p class="muted small lead">
        {{ isSignup ? '관심 팀과 선수를 정하면 홈 화면 맨 위에 먼저 보여 드립니다.' : '관심 팀·선수 소식을 홈 화면에서 먼저 확인하세요.' }}
      </p>

      <div v-if="general || (tried && errorList.length)" ref="summary" class="summary" role="alert" tabindex="-1">
        <p v-if="general" class="summary-title">{{ general }}</p>
        <template v-else>
          <p class="summary-title">입력 내용을 확인해 주세요</p>
          <ul>
            <li v-for="e in errorList" :key="e.field"><a :href="`#auth-${e.field}`">{{ LABELS[e.field] }}: {{ e.msg }}</a></li>
          </ul>
        </template>
      </div>

      <form novalidate @submit.prevent="submit">
        <div class="field">
          <label for="auth-email">이메일</label>
          <input id="auth-email" v-model="form.email" class="input" type="email" inputmode="email" autocomplete="username"
                 autocapitalize="off" spellcheck="false" :aria-invalid="!!errors.email"
                 :aria-describedby="errors.email ? 'auth-email-err' : undefined"
                 @blur="form.email && check('email')" @input="recheck('email')">
          <p v-if="errors.email" id="auth-email-err" class="err">{{ errors.email }}</p>
        </div>

        <div v-if="isSignup" class="field">
          <label for="auth-nickname">닉네임</label>
          <input id="auth-nickname" v-model="form.nickname" class="input" type="text" autocomplete="nickname" maxlength="20"
                 :aria-invalid="!!errors.nickname" aria-describedby="auth-nickname-hint"
                 @blur="form.nickname && check('nickname')" @input="recheck('nickname')">
          <p id="auth-nickname-hint" :class="errors.nickname ? 'err' : 'hint'">{{ errors.nickname || '화면 오른쪽 위에 보이는 이름입니다. 2~20자.' }}</p>
        </div>

        <div class="field">
          <label for="auth-password">비밀번호</label>
          <div class="pw">
            <input id="auth-password" v-model="form.password" class="input" :type="showPw ? 'text' : 'password'"
                   :autocomplete="isSignup ? 'new-password' : 'current-password'" :aria-invalid="!!errors.password"
                   :aria-describedby="isSignup || errors.password ? 'auth-password-hint' : undefined"
                   @blur="form.password && check('password')" @input="recheck('password')">
            <button type="button" class="reveal" :aria-pressed="showPw" @click="showPw = !showPw">
              {{ showPw ? '숨기기' : '보기' }}
            </button>
          </div>
          <p v-if="isSignup || errors.password" id="auth-password-hint" :class="errors.password ? 'err' : 'hint'">
            {{ errors.password || '8자 이상. 숫자로만 된 비밀번호나 흔한 비밀번호, 이메일과 비슷한 비밀번호는 쓸 수 없습니다.' }}
          </p>
        </div>

        <button type="submit" class="btn primary submit" :disabled="busy">
          {{ busy ? (isSignup ? '가입하는 중…' : '로그인하는 중…') : (isSignup ? '가입하기' : '로그인') }}
        </button>
      </form>

      <p class="switch small">
        {{ isSignup ? '이미 계정이 있나요?' : '아직 계정이 없나요?' }}
        <RouterLink :to="other">{{ isSignup ? '로그인' : '회원가입' }}</RouterLink>
      </p>
      <p v-if="isSignup" class="muted small privacy">
        이메일, 닉네임, 암호화한 비밀번호만 저장합니다. 이메일은 로그인에만 쓰고 메일을 보내지 않습니다.
        내 정보 화면에서 언제든 탈퇴할 수 있고, 탈퇴하면 바로 지워집니다.
      </p>
    </section>
  </div>
</template>

<style scoped>
.auth { max-width: 420px; margin: var(--space-4) auto 0; }
.auth .card { padding: var(--space-5); }
.auth h1 { margin-bottom: var(--space-1); }
.lead { margin: 0 0 var(--space-5); }
.summary { border: 1px solid var(--color-bad); border-left-width: 4px; background: #fef2f2; border-radius: 8px;
           padding: var(--space-3) var(--space-4); margin-bottom: var(--space-4); }
.summary:focus-visible { outline-offset: 3px; }
.summary-title { margin: 0; font-weight: 700; color: var(--color-bad); }
.summary ul { margin: 6px 0 0; padding-left: 18px; }
.summary a { color: var(--color-bad); text-decoration: underline; text-underline-offset: 3px; }
.pw { position: relative; }
.pw .input { padding-right: 76px; }
.reveal { position: absolute; right: 4px; top: 50%; transform: translateY(-50%); min-height: 36px; min-width: 60px; padding: 0 10px;
          border: 0; border-radius: 6px; background: transparent; color: var(--color-primary); font: inherit; font-size: .85rem;
          font-weight: 600; cursor: pointer; }
.reveal:hover { background: var(--color-primary-soft); }
.submit { width: 100%; min-height: 46px; margin-top: var(--space-2); }
.switch { text-align: center; margin: var(--space-4) 0 0; }
.privacy { margin: var(--space-3) 0 0; padding-top: var(--space-3); border-top: 1px solid var(--color-border-soft); }
@media (max-width: 480px) { .auth .card { padding: var(--space-4); } }
</style>
