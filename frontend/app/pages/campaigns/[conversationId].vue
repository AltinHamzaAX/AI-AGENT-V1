<script setup lang="ts">
const route = useRoute()
</script>
<template><ChatWorkspace type="campaign" :conversation-id="String(route.params.conversationId)" /></template>
