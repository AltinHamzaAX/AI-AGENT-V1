<script setup lang="ts">
const route = useRoute()
</script>
<template><ChatWorkspace type="post" :conversation-id="String(route.params.conversationId)" /></template>
