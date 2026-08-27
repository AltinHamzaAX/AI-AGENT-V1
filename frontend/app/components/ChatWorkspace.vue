<script setup lang="ts">
import type { ConversationType, GenerationArtifact, GenerationJob, PostGeneration } from '~/types/chat'
import { POST_PROGRESS } from '~/types/chat'

const props = defineProps<{ type: ConversationType; conversationId?: string }>()
const workspace = useChatWorkspace(props.type)
const router = useRouter()
const draft = ref('')
const activeId = ref(props.conversationId)
const section = props.type === 'post' ? 'posts' : 'campaigns'
const activeMessages = computed(() => activeId.value ? workspace.messages[activeId.value] || [] : [])
const postByConversation = useState<Record<string, string>>('post-by-conversation', () => ({}))
const progress = reactive({ running: false, stage: '', result: false, error: '', artifacts: [] as GenerationArtifact[] })

onMounted(async () => {
  await workspace.loadConversations()
  if (activeId.value) await workspace.loadConversation(activeId.value)
})
watch(() => props.conversationId, async (id) => {
  activeId.value = id
  progress.result = false
  progress.error = ''
  progress.artifacts = []
  if (id) await workspace.loadConversation(id)
})

async function newChat() {
  const title = props.type === 'post' ? 'New Post Chat' : 'New Campaign Chat'
  const chat = await workspace.createConversation(title)
  await router.push(`/${section}/${chat.id}`)
}

async function submit() {
  const content = draft.value.trim()
  if (!content) return
  if (!activeId.value) {
    const fallback = props.type === 'post' ? 'New Post Chat' : 'New Campaign Chat'
    const title = content.length > 60 ? `${content.slice(0, 57)}...` : content
    const chat = await workspace.createConversation(title || fallback)
    activeId.value = chat.id
    await router.push(`/${section}/${chat.id}`)
  }
  await workspace.send(activeId.value, content)
  draft.value = ''
}

function stageFromState(state: Record<string, any>): string {
  const stage = state?.state?.supervisor?.current_stage
  return typeof stage === 'string' && POST_PROGRESS.some(item => item[0] === stage) ? stage : progress.stage
}

async function generate() {
  if (!activeId.value || props.type !== 'post') return
  progress.running = true
  progress.result = false
  progress.error = ''
  progress.artifacts = []
  progress.stage = POST_PROGRESS[0][0]
  try {
    const api = useRuntimeConfig().public.apiBaseUrl
    let postId = postByConversation.value[activeId.value]
    if (!postId) {
      const post = await workspace.request<{ id: string }>(`${api}/posts`, {
        method: 'POST',
        body: { conversation_id: activeId.value, title: 'Generated post' },
      })
      postId = post.id
      postByConversation.value[activeId.value] = postId
    }
    const generation = await workspace.request<PostGeneration>(`${api}/posts/${postId}/generations`, { method: 'POST' })
    const deadline = Date.now() + 10 * 60 * 1000
    while (Date.now() < deadline) {
      const job = await workspace.request<GenerationJob>(`${api}/posts/${postId}/generations/${generation.id}/job`)
      try {
        const state = await workspace.request<Record<string, any>>(`${api}/posts/${postId}/generations/${generation.id}/state`)
        progress.stage = stageFromState(state)
      } catch {
        // Workflow state is initialized asynchronously; job status is authoritative.
      }
      if (job.status === 'completed') {
        progress.stage = POST_PROGRESS.at(-1)![0]
        progress.artifacts = await workspace.request<GenerationArtifact[]>(`${api}/posts/${postId}/generations/${generation.id}/artifacts`)
        progress.result = true
        return
      }
      if (job.status === 'failed' || job.status === 'dead') {
        throw new Error(job.last_error_code ? `Generation failed: ${job.last_error_code}` : 'Generation failed.')
      }
      await new Promise(resolve => setTimeout(resolve, 1500))
    }
    throw new Error('Generation is still running. You can check status again shortly.')
  } catch (cause) {
    progress.error = cause instanceof Error ? cause.message : 'Generation failed.'
  } finally {
    progress.running = false
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
      <section v-if="!activeId" class="empty-state"><span class="spark">*</span><h1>What should we create?</h1><p>{{ type === 'post' ? 'Write your brief below. Include the business, objective, audience, offer, platform, and any constraints.' : 'Describe your campaign below. Campaign generation arrives with the Campaign Engine.' }}</p></section>
      <template v-else>
        <section class="messages">
          <div v-if="!activeMessages.length" class="welcome-card"><strong>Start with the outcome.</strong><p>Describe the business, audience, offer, platform, and what success looks like.</p></div>
          <article v-for="message in activeMessages" :key="message.id" class="message" :class="message.role"><span>{{ message.role === 'user' ? 'You' : 'Promotiva' }}</span><p>{{ message.content }}</p></article>
          <section v-if="progress.running || progress.result || progress.error" class="progress-card">
            <h3>{{ progress.error ? 'Generation needs attention' : progress.result ? 'Post ready' : 'Building your post' }}</h3>
            <div v-for="([stage, label], index) in POST_PROGRESS" :key="stage" class="progress-step" :class="{ done: POST_PROGRESS.findIndex(item => item[0] === progress.stage) >= index || progress.result }"><i />{{ label }}</div>
            <p v-if="progress.result">{{ progress.artifacts.length }} output artifact{{ progress.artifacts.length === 1 ? '' : 's' }} produced.</p>
            <p v-if="progress.error" class="error-message">{{ progress.error }}</p>
          </section>
        </section>
      </template>
      <footer class="composer-wrap">
        <div v-if="workspace.attachments.value.length" class="attachment-strip"><figure v-for="item in workspace.attachments.value" :key="item.id"><img :src="item.previewUrl" :alt="item.file.name"><button @click="workspace.removeAttachment(item.id)">x</button><figcaption>{{ item.file.name }}</figcaption></figure></div>
        <form class="composer" @submit.prevent="submit"><textarea v-model="draft" rows="2" :placeholder="type === 'post' ? 'Write a post brief or request a revision...' : 'Describe the campaign...'" @keydown.enter.exact.prevent="submit" /><div class="composer-actions"><label class="attach">+ Images<input type="file" accept="image/*" multiple @change="workspace.addFiles(($event.target as HTMLInputElement).files)"></label><div><button v-if="type === 'post' && activeId" type="button" class="generate" :disabled="progress.running" @click="generate">{{ progress.running ? 'Generating...' : 'Generate post' }}</button><button class="send" :disabled="workspace.busy.value || !draft.trim()">Send</button></div></div></form>
        <p v-if="workspace.error.value" class="error-message">{{ workspace.error.value }}</p>
      </footer>
    </main>
  </div>
</template>
