import { defineConfig } from 'vite'

const buildParts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
}).formatToParts(new Date()).map(({ type, value }) => [type, value]))
const version = `v${buildParts.year}.${buildParts.month}.${buildParts.day} ${buildParts.hour}:${buildParts.minute}`

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  resolve: {
    alias: {
      vue: 'vue/dist/vue.esm-bundler.js',
    },
  },
})
