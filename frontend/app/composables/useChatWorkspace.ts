import type {
  ChatMessage,
  ChatProgress,
  ChatState,
  ChatTurn,
  ChatWorkflow,
  Conversation,
  ConversationContext,
  ConversationType,
  GenerationArtifact,
  GenerationJob,
  PendingAttachment,
} from '~/types/chat'
import { POST_PROGRESS } from '~/types/chat'

const EMPTY_BY_TYPE = (): Record<ConversationType, never[]> => ({ post: [], campaign: [] })
const RUNNING_JOB = new Set(['queued', 'running', 'retry_scheduled'])
const POLL_INTERVAL_MS = 1500
const PENDING_PREFIX = 'pending-'
const POLL_DEADLINE_MS = 10 * 60 * 1000

export function useChatWorkspace(type: ConversationType) {
  const config = useRuntimeConfig()
  const section = type === 'post' ? 'posts' : 'campaigns'
  const api = config.public.apiBaseUrl

  // Nuxt state rather than module scope: on the server this keeps one visitor's
  // chats out of another's request. It also outlives the page components, which
  // the router replaces whenever a chat is opened, so a turn that is still
  // running keeps its progress and its accumulated context.
  const conversationsByType = useState<Record<ConversationType, Conversation[]>>('chat-conversations', EMPTY_BY_TYPE)
  const attachmentsByType = useState<Record<ConversationType, PendingAttachment[]>>('chat-attachments', EMPTY_BY_TYPE)
  const messages = useState<Record<string, ChatMessage[]>>('chat-messages', () => ({}))
  const contexts = useState<Record<string, ConversationContext>>('chat-contexts', () => ({}))
  const progress = useState<Record<string, ChatProgress>>('chat-progress', () => ({}))
  const tracked = useState<string[]>('chat-tracked-generations', () => [])

  const busy = ref(false)
  const error = ref<string | null>(null)
  const conversations = computed(() => conversationsByType.value[type])
  const attachments = computed(() => attachmentsByType.value[type])

  function headers(): Record<string, string> {
    if (!import.meta.client) return {}
    const user = localStorage.getItem('promotiva-user-id') || crypto.randomUUID()
    const project = localStorage.getItem('promotiva-project-id') || crypto.randomUUID()
    localStorage.setItem('promotiva-user-id', user)
    localStorage.setItem('promotiva-project-id', project)
    return { 'X-User-ID': user, 'X-Project-ID': project }
  }

  async function request<T>(url: string, options: { method?: string, body?: unknown } = {}): Promise<T> {
    const isForm = options.body instanceof FormData
    let response: Response
    try {
      response = await fetch(url, {
        method: options.method || 'GET',
        headers: { ...headers(), ...(isForm ? {} : { 'Content-Type': 'application/json' }) },
        body: isForm ? options.body as FormData : options.body != null ? JSON.stringify(options.body) : undefined,
      })
    }
    catch {
      throw new Error('Cannot connect to the Promotiva API. Start the backend on port 8000 and try again.')
    }
    if (!response.ok) throw new Error(await readError(response))
    return await response.json() as T
  }

  async function readError(response: Response): Promise<string> {
    try {
      const body = await response.json() as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
    }
    catch {
      // A non-JSON body carries nothing better than the status itself.
    }
    return `Request failed (${response.status})`
  }

  async function loadConversations() {
    try {
      conversationsByType.value = {
        ...conversationsByType.value,
        [type]: await request<Conversation[]>(`${api}/${section}/conversations`),
      }
    }
    catch (cause) {
      error.value = messageOf(cause, 'Could not load chats.')
    }
  }

  async function createConversation(title: string) {
    const item = await request<Conversation>(`${api}/${section}/conversations`, {
      method: 'POST',
      body: { title, type },
    })
    conversationsByType.value = {
      ...conversationsByType.value,
      [type]: [item, ...conversationsByType.value[type]],
    }
    return item
  }

  /** Load a chat the client just opened, and resume whatever it left running. */
  async function open(id: string) {
    const conversation = await request<Conversation>(`${api}/${section}/conversations/${id}`)
    if (conversation.type !== type) throw new Error('Conversation belongs to another workspace')
    const page = await request<{ items: ChatMessage[] }>(`${api}/${section}/conversations/${id}/messages`)
    // A turn started before this load finished keeps its local echo: the server
    // is the source of truth for what is stored, not for what is in flight.
    const inFlight = (messages.value[id] || []).filter(item => item.id.startsWith(PENDING_PREFIX))
    messages.value = { ...messages.value, [id]: [...page.items, ...inFlight] }
    if (type !== 'post') return
    const state = await request<ChatState>(`${api}/posts/conversations/${id}/state`)
    contexts.value = { ...contexts.value, [id]: state.context }
    if (!state.generation || !state.post_id) return
    const workflow: ChatWorkflow = {
      post_id: state.post_id,
      generation_id: state.generation.id,
      attempt: state.generation.attempt,
      deduplicated: false,
      revises_generation_id: null,
    }
    if (RUNNING_JOB.has(state.generation.job_status)) {
      void track(id, workflow)
      return
    }
    if (state.generation.job_status === 'completed') {
      setProgress(id, {
        running: false,
        stage: POST_PROGRESS.at(-1)![0],
        result: true,
        error: '',
        artifacts: state.artifacts,
      })
    }
  }

  /**
   * Send one client turn.
   *
   * With uploads the message has to exist before the files can be attached to
   * it, so the assistant is asked to answer a stored message; without uploads
   * the whole turn is one round trip.
   */
  async function send(id: string, content: string): Promise<ChatTurn | null> {
    busy.value = true
    error.value = null
    const pending = optimisticMessage(id, content)
    try {
      if (type !== 'post') {
        const message = await request<ChatMessage>(`${api}/${section}/conversations/${id}/messages`, {
          method: 'POST',
          body: { content, role: 'user', metadata: {} },
        })
        await uploadAttachments(message.id)
        replaceMessage(id, pending.id, [message])
        return null
      }
      let turn: ChatTurn
      if (attachments.value.length) {
        const message = await request<ChatMessage>(`${api}/posts/conversations/${id}/messages`, {
          method: 'POST',
          body: { content, role: 'user', metadata: {} },
        })
        await uploadAttachments(message.id)
        turn = await request<ChatTurn>(`${api}/posts/conversations/${id}/turns`, {
          method: 'POST',
          body: { message_id: message.id },
        })
      }
      else {
        turn = await request<ChatTurn>(`${api}/posts/conversations/${id}/turns`, {
          method: 'POST',
          body: { content, metadata: {} },
        })
      }
      replaceMessage(id, pending.id, [turn.user, turn.assistant])
      contexts.value = { ...contexts.value, [id]: turn.context }
      if (turn.workflow) void track(id, turn.workflow)
      return turn
    }
    catch (cause) {
      removeMessage(id, pending.id)
      error.value = messageOf(cause, 'Message failed.')
      throw cause
    }
    finally {
      busy.value = false
    }
  }

  /** Start generation on the client's explicit command rather than by intent. */
  async function startGeneration(id: string) {
    const workflow = await request<ChatWorkflow>(`${api}/posts/conversations/${id}/generations`, { method: 'POST' })
    void track(id, workflow)
    return workflow
  }

  /**
   * Follow one generation to its end.
   *
   * Keyed by generation so a chat reopened mid-run joins the run already in
   * flight instead of starting a second poller against the same job.
   */
  async function track(conversationId: string, workflow: ChatWorkflow) {
    if (tracked.value.includes(workflow.generation_id)) return
    tracked.value = [...tracked.value, workflow.generation_id]
    const base = `${api}/posts/${workflow.post_id}/generations/${workflow.generation_id}`
    setProgress(conversationId, {
      running: true,
      stage: POST_PROGRESS[0][0],
      result: false,
      error: '',
      artifacts: [],
    })
    const deadline = Date.now() + POLL_DEADLINE_MS
    try {
      while (Date.now() < deadline) {
        const job = await request<GenerationJob>(`${base}/job`)
        patchProgress(conversationId, { stage: await currentStage(base, conversationId) })
        if (job.status === 'completed') {
          patchProgress(conversationId, {
            running: false,
            stage: POST_PROGRESS.at(-1)![0],
            result: true,
            artifacts: await request<GenerationArtifact[]>(`${base}/artifacts`),
          })
          return
        }
        if (job.status === 'failed' || job.status === 'dead') {
          throw new Error(await failureReason(base, job))
        }
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS))
      }
      throw new Error('Generation is still running. You can check the status again shortly.')
    }
    catch (cause) {
      patchProgress(conversationId, { running: false, error: messageOf(cause, 'Generation failed.') })
    }
    finally {
      patchProgress(conversationId, { running: false })
      tracked.value = tracked.value.filter(item => item !== workflow.generation_id)
    }
  }

  /**
   * Why a generation stopped, in the workflow's own words.
   *
   * The job only records how it ended; the Supervisor records what it decided,
   * which is the difference between "non_retryable" and a sentence a person can
   * act on.
   */
  async function failureReason(base: string, job: GenerationJob): Promise<string> {
    try {
      const state = await request<{ state?: { supervisor?: { last_decision?: { reason?: unknown } } } }>(`${base}/state`)
      const reason = state.state?.supervisor?.last_decision?.reason
      if (typeof reason === 'string' && reason) return `Generation stopped: ${reason}.`
    }
    catch {
      // The job status stays authoritative when the state cannot be read.
    }
    return job.last_error_code ? `Generation failed: ${job.last_error_code}` : 'Generation failed.'
  }

  async function currentStage(base: string, conversationId: string): Promise<string> {
    const fallback = progress.value[conversationId]?.stage || POST_PROGRESS[0][0]
    try {
      const state = await request<{ state?: { supervisor?: { current_stage?: unknown } } }>(`${base}/state`)
      const stage = state.state?.supervisor?.current_stage
      return typeof stage === 'string' && POST_PROGRESS.some(item => item[0] === stage) ? stage : fallback
    }
    catch {
      // Workflow state is initialized asynchronously; job status is authoritative.
      return fallback
    }
  }

  function setProgress(conversationId: string, value: ChatProgress) {
    progress.value = { ...progress.value, [conversationId]: value }
  }

  function patchProgress(conversationId: string, value: Partial<ChatProgress>) {
    const current = progress.value[conversationId]
    if (!current) return
    progress.value = { ...progress.value, [conversationId]: { ...current, ...value } }
  }

  async function uploadAttachments(messageId: string) {
    for (const item of attachments.value) {
      const body = new FormData()
      body.append('message_id', messageId)
      body.append('role', 'reference')
      body.append('file', item.file)
      await request(`${api}/assets`, { method: 'POST', body })
    }
    clearAttachments()
  }

  function optimisticMessage(conversationId: string, content: string): ChatMessage {
    const message: ChatMessage = {
      id: `${PENDING_PREFIX}${crypto.randomUUID()}`,
      conversation_id: conversationId,
      sequence: (messages.value[conversationId]?.length || 0) + 1,
      role: 'user',
      content,
      metadata: {},
      created_at: new Date().toISOString(),
    }
    messages.value = {
      ...messages.value,
      [conversationId]: [...(messages.value[conversationId] || []), message],
    }
    return message
  }

  function replaceMessage(conversationId: string, pendingId: string, actual: ChatMessage[]) {
    const current = messages.value[conversationId] || []
    const known = new Set(actual.map(item => item.id))
    messages.value = {
      ...messages.value,
      [conversationId]: [
        ...current.filter(item => item.id !== pendingId && !known.has(item.id)),
        ...actual,
      ],
    }
  }

  function removeMessage(conversationId: string, pendingId: string) {
    const current = messages.value[conversationId] || []
    messages.value = { ...messages.value, [conversationId]: current.filter(item => item.id !== pendingId) }
  }

  function addFiles(files: FileList | null) {
    const added = Array.from(files || [])
      .filter(file => file.type.startsWith('image/'))
      .map(file => ({ id: crypto.randomUUID(), file, previewUrl: URL.createObjectURL(file) }))
    if (added.length) setAttachments([...attachments.value, ...added])
  }

  function removeAttachment(id: string) {
    const item = attachments.value.find(entry => entry.id === id)
    if (item) URL.revokeObjectURL(item.previewUrl)
    setAttachments(attachments.value.filter(entry => entry.id !== id))
  }

  function clearAttachments() {
    attachments.value.forEach(item => URL.revokeObjectURL(item.previewUrl))
    setAttachments([])
  }

  function setAttachments(items: PendingAttachment[]) {
    attachmentsByType.value = { ...attachmentsByType.value, [type]: items }
  }

  function messageOf(cause: unknown, fallback: string) {
    return cause instanceof Error && cause.message ? cause.message : fallback
  }

  return {
    conversations,
    messages,
    contexts,
    progress,
    attachments,
    busy,
    error,
    loadConversations,
    createConversation,
    open,
    send,
    startGeneration,
    addFiles,
    removeAttachment,
    clearAttachments,
  }
}
