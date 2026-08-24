<script setup lang="ts">
type HealthResponse = {
  status: string
  service: string
}

const config = useRuntimeConfig()
const { data, error, status, refresh } = await useFetch<HealthResponse>(
  `${config.public.apiBaseUrl}/health`,
  { server: false },
)

const backendStatus = computed(() => {
  if (status.value === 'pending') return 'Checking…'
  if (error.value) return 'Unavailable'
  return data.value?.status === 'ok' ? 'Connected' : 'Unknown'
})
</script>

<template>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Promotiva</p>
      <h1>AI Marketing Platform</h1>
      <p class="intro">
        A modular foundation for intelligent posts and campaigns.
      </p>
      <div class="status-card" :class="{ error: error }">
        <span class="status-dot" aria-hidden="true" />
        <span>Backend status: {{ backendStatus }}</span>
        <button v-if="error" type="button" @click="refresh()">Retry</button>
      </div>
    </section>
  </main>
</template>
