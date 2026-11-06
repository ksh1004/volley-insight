<script setup>
// WEB-11/12: my account. Pick favourite teams of this season, see and remove favourite players, log out, delete account.
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getTeams } from '@/api/client'
import { GENDERS } from '@/constants/volley'
import { useAuthStore } from '@/stores/auth'
import { fmtFullDate } from '@/utils/format'

const auth = useAuthStore()
const router = useRouter()
const teams = ref([])
const pending = ref('') // `${kind}:${code}` being saved
const status = ref('')
const error = ref('')

onMounted(async () => {
  try {
    teams.value = await getTeams()
  } catch {
    /* the picker just stays empty */
  }
})
const teamsBy = computed(() => ['M', 'F'].map((g) => ({ g, teams: teams.value.filter((t) => t.gender === g) })))

async function toggle(kind, item) {
  pending.value = `${kind}:${item.code}`
  error.value = ''
  const was = auth.isFavorite(kind, item.code)
  try {
    await auth.toggleFavorite(kind, item.code)
    status.value = `${item.name}: ${was ? '관심 목록에서 뺐습니다' : '관심 목록에 추가했습니다'}`
  } catch (e) {
    error.value = e.response?.data?.detail || '저장하지 못했습니다. 다시 시도해 주세요.'
  } finally {
    pending.value = ''
  }
}

async function logout() {
  await auth.logout()
  router.replace('/')
}

// account deletion asks for the password again
const deleting = ref(false)
const password = ref('')
const deleteError = ref('')
const busy = ref(false)
async function deleteAccount() {
  if (!password.value) {
    deleteError.value = '비밀번호를 입력하세요.'
    return
  }
  busy.value = true
  deleteError.value = ''
  try {
    await auth.deleteAccount(password.value)
    router.replace('/')
  } catch (e) {
    deleteError.value = [].concat(e.response?.data?.password || '탈퇴하지 못했습니다. 다시 시도해 주세요.').join(' ')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="title-row">
    <div>
      <h1>내 정보</h1>
      <p class="muted small">{{ auth.user?.email }} · {{ auth.user ? fmtFullDate(auth.user.date_joined) : '' }} 가입</p>
    </div>
    <button type="button" class="btn" @click="logout">로그아웃</button>
  </div>

  <span class="sr-only" role="status">{{ status }}</span>
  <p v-if="error" class="err" role="alert">{{ error }}</p>

  <section class="card">
    <div class="card-head">
      <h2>관심 팀 <span class="muted small">{{ auth.favorites.teams.length }}/10 · 홈 화면 맨 위에 먼저 보입니다</span></h2>
    </div>
    <div v-for="grp in teamsBy" :key="grp.g" class="group">
      <h3 class="small muted">{{ GENDERS[grp.g] }}</h3>
      <div class="chips" role="group" :aria-label="`${GENDERS[grp.g]} 관심 팀`">
        <button v-for="t in grp.teams" :key="t.code" type="button" class="tab" :aria-pressed="auth.isFavorite('team', t.code)"
                :disabled="pending === `team:${t.code}`" @click="toggle('team', t)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" /></svg>
          {{ t.name }}
        </button>
      </div>
    </div>
    <p v-if="!teams.length" class="empty">팀 목록을 불러오지 못했습니다.</p>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>관심 선수 <span class="muted small">{{ auth.favorites.players.length }}/10</span></h2>
    </div>
    <ul v-if="auth.favorites.players.length" class="players">
      <li v-for="p in auth.favorites.players" :key="p.code">
        <RouterLink :to="`/players/${p.code}`" class="name">{{ p.name }}</RouterLink>
        <span class="muted small">{{ GENDERS[p.gender] }}</span>
        <button type="button" class="btn small-btn" :disabled="pending === `player:${p.code}`"
                :aria-label="`${p.name} 관심 선수에서 빼기`" @click="toggle('player', p)">빼기</button>
      </li>
    </ul>
    <p v-else class="empty">아직 없습니다. 선수 화면의 <b>관심 선수</b> 버튼으로 추가하세요. 위쪽 검색창에서 선수를 찾을 수 있습니다.</p>
  </section>

  <section class="card danger-zone">
    <h2>회원 탈퇴</h2>
    <p class="muted small">계정과 관심 팀·선수 정보를 바로 지웁니다. 되돌릴 수 없습니다.</p>
    <button v-if="!deleting" type="button" class="btn danger" @click="deleting = true">탈퇴하기</button>
    <form v-else class="confirm" novalidate @submit.prevent="deleteAccount">
      <div class="field">
        <label for="delete-password">비밀번호 확인</label>
        <input id="delete-password" v-model="password" class="input" type="password" autocomplete="current-password"
               :aria-invalid="!!deleteError" :aria-describedby="deleteError ? 'delete-err' : undefined">
        <p v-if="deleteError" id="delete-err" class="err" role="alert">{{ deleteError }}</p>
      </div>
      <div class="row">
        <button type="submit" class="btn danger" :disabled="busy">{{ busy ? '지우는 중…' : '계정 지우기' }}</button>
        <button type="button" class="btn" @click="deleting = false; password = ''; deleteError = ''">취소</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-bottom: var(--space-3); }
.title-row h1 { margin-bottom: 4px; }
.title-row p { margin: 0; }
.group + .group { margin-top: var(--space-3); }
.group h3 { margin: 0 0 6px; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips .tab { display: inline-flex; align-items: center; gap: 6px; }
.chips svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linejoin: round; }
.chips .tab[aria-pressed='true'] { background: var(--color-accent-soft); border-color: var(--color-accent); color: var(--color-accent); font-weight: 700; }
.chips .tab[aria-pressed='true'] svg { fill: currentColor; }
.players { list-style: none; margin: 0; padding: 0; }
.players li { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--color-border-soft); }
.players .name { font-weight: 600; }
.small-btn { margin-left: auto; min-height: 34px; padding: 0 12px; font-size: .85rem; }
.err { color: var(--color-bad); }
.danger-zone h2 { color: var(--color-bad); }
.danger-zone > p { margin: 0 0 var(--space-3); }
.confirm { max-width: 360px; }
.row { display: flex; gap: 8px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (pointer: coarse) { .small-btn { min-height: 44px; } }
</style>
