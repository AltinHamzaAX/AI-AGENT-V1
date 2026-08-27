<script setup lang="ts">
import type { ChatMessage, ConversationType } from '~/types/chat'
import { CONTEXT_LABELS, INTENT_LABELS, POST_PROGRESS } from '~/types/chat'

const props = defineProps<{ type: ConversationType, conversationId?: string }>()
const workspace = useChatWorkspace(props.type)
const router = useRouter()
const draft = ref('')
const section = props.type === 'post' ? 'posts' : 'campaigns'

const activeId = computed(() => props.conversationId)
const activeMessages = computed(() => activeId.value ? workspace.messages.value[activeId.value] || [] : [])
const context = computed(() => activeId.value ? workspace.contexts.value[activeId.value] : undefined)
const progress = computed(() => activeId.value ? workspace.progress.value[activeId.value] : undefined)
const knownContext = computed(() => CONTEXT_LABELS
  .map(([key, label]) => [label, context.value?.[key]] as const)
  .filter((entry): entry is readonly [string, string] => typeof entry[1] === 'string' && entry[1].length > 0))
const stageIndex = computed(() => POST_PROGRESS.findIndex(item => item[0] === progress.value?.stage))

/** Uploads are stored against the message that carried them. */
function attachmentsFor(message: ChatMessage) {
  return (context.value?.attachments || []).filter(asset => asset.message_id === message.id)
}

function intentOf(message: ChatMessage) {
  const intent = message.metadata?.chat?.intent
  return intent ? INTENT_LABELS[intent] : null
}

// Opening a chat replaces this page component, so both entry points reload the
// chat the same way. The workspace keeps any turn already in flight.
onMounted(async () => {
  await workspace.loadConversations()
  if (activeId.value) await workspace.open(activeId.value)
})

watch(() => props.conversationId, async (id) => {
  if (id) await workspace.open(id)
})

async function newChat() {
  const title = props.type === 'post' ? 'New Post Chat' : 'New Campaign Chat'
  const chat = await workspace.createConversation(title)
  await router.push(`/${section}/${chat.id}`)
}

async function submit() {
  const content = draft.value.trim()
  if (!content || workspace.busy.value) return
  let conversationId = activeId.value
  if (!conversationId) {
    const fallback = props.type === 'post' ? 'New Post Chat' : 'New Campaign Chat'
    const title = content.length > 60 ? `${content.slice(0, 57)}...` : content
    const chat = await workspace.createConversation(title || fallback)
    conversationId = chat.id
    await router.push(`/${section}/${chat.id}`)
  }
  const sent = draft.value
  draft.value = ''
  try {
    await workspace.send(conversationId, content)
  }
  catch {
    draft.value = sent
  }
}

async function generate() {
  if (!activeId.value || props.type !== 'post' || progress.value?.running) return
  await workspace.startGeneration(activeId.value)
}
</script>

<template>
  <div class="app-frame">
    <header class="topbar">
      <NuxtLink to="/posts" class="brand">Promotiva</NuxtLink>
      <nav class="section-switch"><NuxtLink to="/posts" :class="{ active: type === 'post' }">Posts</NuxtLink><NuxtLink to="/campaigns" :class="{ active: type === 'campaign' }">Campaigns</NuxtLink></nav>
      <span class="mode-pill">{{ type }} studio</span>
    </header>
    <aside class="sidebar">
      <button class="new-chat" @click="newChat">+ New {{ type === 'post' ? 'Post' : 'Campaign' }} Chat</button>
      <p class="sidebar-label">{{ section }}</p>
      <NuxtLink v-for="chat in workspace.conversations.value" :key="chat.id" :to="`/${section}/${chat.id}`" class="chat-link" :class="{ active: chat.id === activeId }"><span>{{ chat.title || `Untitled ${type}` }}</span><small>{{ new Date(chat.updated_at).toLocaleDateString() }}</small></NuxtLink>
    </aside>
    <main class="conversation-pane">
      <section v-if="!activeId" class="empty-state"><span class="spark">*</span><h1>What should we create?</h1><p>{{ type === 'post' ? 'Tell me about your business and what you want to promote. Ask questions, share images, and I will build the post when the brief is ready.' : 'Describe your campaign below. Campaign generation arrives with the Campaign Engine.' }}</p></section>
      <template v-else>
        <section class="messages">
          <div v-if="knownContext.length" class="context-bar">
            <span v-for="[label, value] in knownContext" :key="label" class="context-chip"><b>{{ label }}</b>{{ value }}</span>
          </div>
          <div v-if="!activeMessages.length" class="welcome-card"><strong>Start anywhere.</strong><p>Say what your business is, ask for advice, or ask straight for a post. I only start generating when you ask and the brief is complete.</p></div>
          <article v-for="message in activeMessages" :key="message.id" class="message" :class="message.role">
            <span>{{ message.role === 'user' ? 'You' : 'Promotiva' }}<i v-if="intentOf(message)" class="intent-chip">{{ intentOf(message) }}</i></span>
            <p>{{ message.content }}</p>
            <div v-if="attachmentsFor(message).length" class="message-assets"><span v-for="asset in attachmentsFor(message)" :key="asset.id">{{ asset.original_filename }}</span></div>
          </article>
          <section v-if="progress && (progress.running || progress.result || progress.error)" class="progress-card">
            <h3>{{ progress.error ? 'Generation needs attention' : progress.result ? 'Post ready' : 'Building your post' }}</h3>
            <div v-for="([stage, label], index) in POST_PROGRESS" :key="stage" class="progress-step" :class="{ done: stageIndex >= index || progress.result }"><i />{{ label }}</div>
            <p v-if="progress.result">{{ progress.artifacts.length }} output artifact{{ progress.artifacts.length === 1 ? '' : 's' }} produced. Ask for any change and I will revise it.</p>
            <p v-if="progress.error" class="error-message">{{ progress.error }}</p>
          </section>
        </section>
      </template>
      <footer class="composer-wrap">
        <div v-if="workspace.attachments.value.length" class="attachment-strip"><figure v-for="item in workspace.attachments.value" :key="item.id"><img :src="item.previewUrl" :alt="item.file.name"><button @click="workspace.removeAttachment(item.id)">x</button><figcaption>{{ item.file.name }}</figcaption></figure></div>
        <form class="composer" @submit.prevent="submit"><textarea v-model="draft" rows="2" :placeholder="type === 'post' ? 'Ask, discuss, or request the post...' : 'Describe the campaign...'" @keydown.enter.exact.prevent="submit" /><div class="composer-actions"><label class="attach">+ Images<input type="file" accept="image/*" multiple @change="workspace.addFiles(($event.target as HTMLInputElement).files)"></label><div><button v-if="type === 'post' && activeId" type="button" class="generate" :disabled="progress?.running" @click="generate">{{ progress?.running ? 'Generating...' : 'Generate post' }}</button><button class="send" :disabled="workspace.busy.value || !draft.trim()">Send</button></div></div></form>
        <p v-if="workspace.error.value" class="error-message">{{ workspace.error.value }}</p>
      </footer>
    </main>
  </div>
</template>
