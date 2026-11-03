<template>
  <header class="app-header">
    <RouterLink to="/" class="brand">배구 전력 분석</RouterLink>
  </header>
  <main class="app-main">
    <RouterView />
  </main>
</template>

<style>
body { margin: 0; font-family: 'Malgun Gothic', system-ui, sans-serif; color: #172131; background: #f4f6f9; }
.app-header { padding: 12px 20px; background: #fff; border-bottom: 1px solid #dde2ea; }
.brand { font-weight: 700; color: #1c5bc9; text-decoration: none; }
.app-main { max-width: 1080px; margin: 0 auto; padding: 24px 16px; }
</style>
