<script setup lang="ts">
import type { CampaignStatus, ChatMessage, ConversationType } from '~/types/chat'
import { CONTEXT_LABELS, INTENT_LABELS, POST_PROGRESS, postProgressIndex } from '~/types/chat'

const props = defineProps<{ type: ConversationType, conversationId?: string }>()
const workspace = useChatWorkspace(props.type)
const router = useRouter()
const draft = ref('')
const creating = ref(false)
const section = props.type === 'post' ? 'posts' : 'campaigns'

const activeId = computed(() => props.conversationId)
const activeMessages = computed(() => activeId.value ? workspace.messages.value[activeId.value] || [] : [])
const context = computed(() => activeId.value ? workspace.contexts.value[activeId.value] : undefined)
const campaign = computed(() => activeId.value ? workspace.campaignStates.value[activeId.value] : undefined)
const progress = computed(() => activeId.value ? workspace.progress.value[activeId.value] : undefined)
const activity = computed(() => activeId.value ? workspace.activity.value[activeId.value] || 'idle' : 'idle')
const knownContext = computed(() => CONTEXT_LABELS
  .map(([key, label]) => [label, context.value?.[key]] as const)
  .filter((entry): entry is readonly [string, string] => typeof entry[1] === 'string' && entry[1].length > 0))
const stageIndex = computed(() => postProgressIndex(progress.value?.stage))
const processingLabel = computed(() => {
  if (activity.value === 'sending') return 'Sending your message...'
  if (activity.value === 'thinking') return 'Promotiva is thinking...'
  if (activity.value === 'responding') return 'Promotiva is responding...'
  return ''
})
const campaignStatusLabel = computed(() => {
  const status: CampaignStatus | undefined = campaign.value?.status
  if (status === 'READY') return 'Ready for Campaign Plan generation'
  if (status === 'GENERATING') return 'Campaign Plan is being generated'
  if (status === 'PLAN_READY') return 'Campaign Plan ready'
  return 'Still collecting campaign information'
})
const campaignBriefItems = computed(() => {
  const brief = campaign.value?.brief
  if (!brief) return []
  return [
    ['Business', brief.business || brief.product_or_service],
    ['Goal', brief.goal],
    ['Audience', brief.audience],
    ['Location', brief.location],
  ].filter((item): item is [string, string] => typeof item[1] === 'string' && item[1].length > 0)
})

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
  if (!content || workspace.busy.value || creating.value) return
  let conversationId = activeId.value
  if (!conversationId) {
    creating.value = true
    const fallback = props.type === 'post' ? 'New Post Chat' : 'New Campaign Chat'
    const title = content.length > 60 ? `${content.slice(0, 57)}...` : content
    try {
      const chat = await workspace.createConversation(title || fallback)
      conversationId = chat.id
      await router.push(`/${section}/${chat.id}`)
    }
    catch (cause) {
      workspace.error.value = cause instanceof Error ? cause.message : 'Could not start the chat.'
      creating.value = false
      return
    }
    creating.value = false
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
          <section v-if="type === 'campaign' && campaign" class="campaign-status" :class="campaign.status.toLowerCase()" aria-live="polite">
            <div><strong>{{ campaignStatusLabel }}</strong><span v-if="campaign.status === 'READY'">You can generate a plan when that action is available.</span></div>
            <div v-if="campaignBriefItems.length" class="campaign-brief"><span v-for="[label, value] in campaignBriefItems" :key="label"><b>{{ label }}</b>{{ value }}</span></div>
          </section>
          <div v-if="knownContext.length" class="context-bar">
            <span v-for="[label, value] in knownContext" :key="label" class="context-chip"><b>{{ label }}</b>{{ value }}</span>
          </div>
          <div v-if="!activeMessages.length" class="welcome-card"><strong>Start anywhere.</strong><p>Say what your business is, ask for advice, or request a post naturally. I will ask only for critical missing details and start automatically when your brief is ready.</p></div>
          <article v-for="message in activeMessages" :key="message.id" class="message" :class="message.role">
            <span>{{ message.role === 'user' ? 'You' : 'Promotiva' }}<i v-if="intentOf(message)" class="intent-chip">{{ intentOf(message) }}</i></span>
            <p>{{ message.content }}</p>
            <div v-if="attachmentsFor(message).length" class="message-assets"><span v-for="asset in attachmentsFor(message)" :key="asset.id">{{ asset.original_filename }}</span></div>
          </article>
          <article v-if="processingLabel" class="message assistant processing" aria-live="polite" aria-busy="true">
            <span>Promotiva</span><p><i class="typing-dots"><b /><b /><b /></i>{{ processingLabel }}</p>
          </article>
          <section v-if="progress && (progress.running || progress.result || progress.error)" class="progress-card">
            <h3>{{ progress.error ? 'Generation needs attention' : progress.result ? 'Post ready' : 'Building your post' }}</h3>
            <div v-for="([stage, label], index) in POST_PROGRESS" :key="stage" class="progress-step" :class="{ done: progress.result || stageIndex > index, active: !progress.result && stageIndex === index }"><i />{{ label }}</div>
            <div v-if="progress.result" class="generated-artifacts">
              <img v-for="artifact in progress.artifacts.filter(item => item.preview_url)" :key="artifact.id" :src="artifact.preview_url" alt="Generated Promotiva post">
              <p>{{ progress.artifacts.length }} output artifact{{ progress.artifacts.length === 1 ? '' : 's' }} produced. Ask for any change and I will revise it.</p>
            </div>
            <p v-if="progress.error" class="error-message">{{ progress.error }}</p>
          </section>
        </section>
      </template>
      <footer class="composer-wrap">
        <div v-if="type === 'post' && workspace.attachments.value.length" class="attachment-strip"><figure v-for="item in workspace.attachments.value" :key="item.id"><img :src="item.previewUrl" :alt="item.file.name"><button @click="workspace.removeAttachment(item.id)">x</button><figcaption>{{ item.file.name }}</figcaption></figure></div>
        <form class="composer" @submit.prevent="submit"><textarea v-model="draft" rows="2" :placeholder="type === 'post' ? 'Ask, discuss, or request the post...' : 'Describe the campaign...'" @keydown.enter.exact.prevent="submit" /><div class="composer-actions"><label v-if="type === 'post'" class="attach">+ Images<input type="file" accept="image/*" multiple @change="workspace.addFiles(($event.target as HTMLInputElement).files)"></label><span v-else class="campaign-hint">Campaign briefing</span><button class="send" :disabled="workspace.busy.value || !draft.trim()">Send</button></div></form>
        <p v-if="workspace.error.value" class="error-message">{{ workspace.error.value }}</p>
      </footer>
    </main>
  </div>
</template>
