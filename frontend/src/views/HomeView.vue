<script setup>
import { onMounted, ref } from 'vue'
import { getHealth } from '@/api/client'

// Temporary home screen: confirms the frontend -> Django -> PostgreSQL path works.
const health = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    health.value = await getHealth()
  } catch (e) {
    error.value = e.message
  }
})
</script>

<template>
  <h1>개발 환경 확인</h1>
  <p v-if="health">API: {{ health.status }} · DB: {{ health.database }}</p>
  <p v-else-if="error">API에 연결하지 못했습니다: {{ error }}</p>
  <p v-else>API 확인 중…</p>
</template>
