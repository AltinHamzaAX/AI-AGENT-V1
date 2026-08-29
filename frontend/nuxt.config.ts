const apiProxyTarget = process.env.NUXT_API_PROXY_TARGET || 'http://127.0.0.1:8000/api'

export default defineNuxtConfig({
  compatibilityDate: '2026-08-24',
  devtools: { enabled: true },
  css: ['~/assets/css/main.css', '~/assets/css/chat-processing.css'],
  runtimeConfig: {
    public: {
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL || '/api',
    },
  },
  routeRules: {
    '/api/**': { proxy: `${apiProxyTarget}/**` },
  },
  typescript: {
    strict: true,
    typeCheck: true,
  },
})
