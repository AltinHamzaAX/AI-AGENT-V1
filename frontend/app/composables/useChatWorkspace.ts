import type {
  ChatMessage,
  ChatActivityState,
  CampaignCreate,
  CampaignDetail,
  CampaignMessageResponse,
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
import { POST_PROGRESS, POST_STAGE_TO_PROGRESS } from '~/types/chat'

const EMPTY_BY_TYPE = (): Record<ConversationType, never[]> => ({ post: [], campaign: [] })
const RUNNING_JOB = new Set(['queued', 'running', 'retry_scheduled'])
const POLL_INTERVAL_MS = 1500
const PENDING_PREFIX = 'pending-'
// The backend owns the authoritative one-hour generation timeout. A complete
// agency workflow can exceed ten minutes on a local LLM, especially when an
// agent uses its bounded repair pass, so the client must not report a failure
// while the leased job is still running.
const POLL_DEADLINE_MS = 65 * 60 * 1000

class WorkspaceRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

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
  const campaignIds = useState<Record<string, string>>('campaign-ids', () => ({}))
  const campaignStates = useState<Record<string, CampaignDetail>>('campaign-states', () => ({}))
  const campaignSending = useState<Record<string, boolean>>('campaign-sending', () => ({}))
  const progress = useState<Record<string, ChatProgress>>('chat-progress', () => ({}))
  const activity = useState<Record<string, ChatActivityState>>('chat-activity', () => ({}))
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
    if (!response.ok) throw new WorkspaceRequestError(await readError(response), response.status)
    return await response.json() as T
  }

  async function readError(response: Response): Promise<string> {
    if (type === 'campaign') return campaignError(response.status)
    try {
      const body = await response.json() as { detail?: unknown }
      if (typeof body.detail === 'string') return body.detail
      if (body.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
        const detail = body.detail as { message?: unknown }
        if (typeof detail.message === 'string') return detail.message
      }
      if (Array.isArray(body.detail)) {
        const messages = body.detail
          .map(item => item && typeof item === 'object' ? (item as { msg?: unknown }).msg : null)
          .filter((message): message is string => typeof message === 'string')
        if (messages.length) return messages.join('; ')
      }
    }
    catch {
      // A non-JSON body carries nothing better than the status itself.
    }
    return `Request failed (${response.status})`
  }

  function campaignError(status: number): string {
    return {
      404: 'Campaign chat was not found.',
      409: 'This campaign cannot be updated right now.',
      422: 'Please enter a valid campaign message.',
      429: 'The campaign assistant is busy. Please try again shortly.',
      500: 'The campaign request failed. Please try again.',
      502: 'The campaign assistant is temporarily unavailable. Please try again.',
      503: 'The campaign service is temporarily unavailable. Please try again.',
    }[status] || 'The campaign request could not be completed.'
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
    if (type === 'campaign') {
      try {
        await resolveCampaign(item.id, true)
      }
      catch (cause) {
        // Keep the new conversation addressable so retry can finish setup
        // without creating another conversation.
        error.value = messageOf(cause, 'Could not set up the Campaign chat.')
      }
    }
    return item
  }

  function rememberCampaign(conversationId: string, campaignId: string) {
    campaignIds.value = { ...campaignIds.value, [conversationId]: campaignId }
    if (import.meta.client) localStorage.setItem(`promotiva-campaign-${conversationId}`, campaignId)
  }

  function forgetCampaign(conversationId: string) {
    const next = { ...campaignIds.value }
    delete next[conversationId]
    campaignIds.value = next
    if (import.meta.client) localStorage.removeItem(`promotiva-campaign-${conversationId}`)
  }

  async function loadCampaign(conversationId: string, campaignId: string): Promise<CampaignDetail> {
    const detail = await request<CampaignDetail>(`${api}/campaigns/${campaignId}`)
    campaignStates.value = { ...campaignStates.value, [conversationId]: detail }
    rememberCampaign(conversationId, detail.id)
    return detail
  }

  async function findCampaign(conversationId: string): Promise<CampaignCreate> {
    return await request<CampaignCreate>(
      `${api}/campaigns?conversation_id=${encodeURIComponent(conversationId)}`,
    )
  }

  async function resolveCampaign(
    conversationId: string,
    createIfMissing: boolean,
  ): Promise<CampaignDetail | undefined> {
    const cachedId = rememberedCampaignId(conversationId)
    if (cachedId) {
      try {
        return await loadCampaign(conversationId, cachedId)
      }
      catch (cause) {
        if (!(cause instanceof WorkspaceRequestError) || cause.status !== 404) throw cause
        forgetCampaign(conversationId)
      }
    }

    let identity: CampaignCreate
    try {
      identity = await findCampaign(conversationId)
    }
    catch (cause) {
      if (!(cause instanceof WorkspaceRequestError) || cause.status !== 404) throw cause
      if (!createIfMissing) return undefined
      try {
        identity = await request<CampaignCreate>(`${api}/campaigns`, {
          method: 'POST',
          body: { conversation_id: conversationId },
        })
      }
      catch (creationCause) {
        if (!(creationCause instanceof WorkspaceRequestError) || creationCause.status !== 409) {
          throw creationCause
        }
        // Another request may have completed creation after our lookup.
        identity = await findCampaign(conversationId)
      }
    }

    rememberCampaign(conversationId, identity.id)
    return await loadCampaign(conversationId, identity.id)
  }

  function rememberedCampaignId(conversationId: string): string | undefined {
    const known = campaignIds.value[conversationId]
    if (known) return known
    if (!import.meta.client) return undefined
    const stored = localStorage.getItem(`promotiva-campaign-${conversationId}`)
    if (stored) campaignIds.value = { ...campaignIds.value, [conversationId]: stored }
    return stored || undefined
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
    if (type !== 'post') {
      await resolveCampaign(id, false)
      return
    }
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
      setActivity(id, 'generating')
      void track(id, workflow)
      return
    }
    if (state.generation.job_status === 'completed') {
      setActivity(id, 'completed')
      setProgress(id, {
        running: false,
        stage: POST_PROGRESS.at(-1)![0],
        result: true,
        error: '',
        artifacts: await hydrateArtifacts(workflow, state.artifacts),
      })
      return
    }
    if (state.generation.job_status === 'failed' || state.generation.job_status === 'dead') {
      const base = `${api}/posts/${workflow.post_id}/generations/${workflow.generation_id}`
      const job = await request<GenerationJob>(`${base}/job`)
      setActivity(id, 'failed')
      setProgress(id, {
        running: false,
        stage: POST_PROGRESS[0][0],
        result: false,
        error: await failureReason(base, job),
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
    if (type === 'campaign' && campaignSending.value[id]) return null
    if (type === 'campaign') campaignSending.value = { ...campaignSending.value, [id]: true }
    busy.value = true
    error.value = null
    setActivity(id, 'sending')
    const pending = optimisticMessage(id, content)
    try {
      if (type !== 'post') {
        const campaign = await resolveCampaign(id, true)
        if (!campaign) throw new Error('Campaign chat setup is unavailable. Please try again.')
        setActivity(id, 'thinking')
        const response = await request<CampaignMessageResponse>(`${api}/campaigns/${campaign.id}/messages`, {
          method: 'POST',
          body: { message: content },
        })
        const sequence = pending.sequence
        const userMessage = { ...pending, id: `campaign-user-${crypto.randomUUID()}`, sequence }
        const assistantMessage: ChatMessage = {
          id: `campaign-assistant-${crypto.randomUUID()}`,
          conversation_id: id,
          sequence: sequence + 1,
          role: 'assistant',
          content: response.reply,
          metadata: {},
          created_at: new Date().toISOString(),
        }
        replaceMessage(id, pending.id, [userMessage, assistantMessage])
        const current = campaignStates.value[id]
        if (current) campaignStates.value = {
          ...campaignStates.value,
          [id]: { ...current, status: response.status, brief: response.brief },
        }
        setActivity(id, 'idle')
        return null
      }
      let turn: ChatTurn
      if (attachments.value.length) {
        const message = await request<ChatMessage>(`${api}/posts/conversations/${id}/messages`, {
          method: 'POST',
          body: { content, role: 'user', metadata: {} },
        })
        await uploadAttachments(message.id)
        setActivity(id, 'thinking')
        turn = await request<ChatTurn>(`${api}/posts/conversations/${id}/turns`, {
          method: 'POST',
          body: { message_id: message.id },
        })
      }
      else {
        setActivity(id, 'thinking')
        turn = await request<ChatTurn>(`${api}/posts/conversations/${id}/turns`, {
          method: 'POST',
          body: { content, metadata: {} },
        })
      }
      setActivity(id, 'responding')
      replaceMessage(id, pending.id, [turn.user, turn.assistant])
      contexts.value = { ...contexts.value, [id]: turn.context }
      await nextTick()
      if (turn.workflow) {
        setActivity(id, 'generating')
        void track(id, turn.workflow)
      }
      else setActivity(id, 'idle')
      return turn
    }
    catch (cause) {
      removeMessage(id, pending.id)
      error.value = messageOf(cause, 'Message failed.')
      setActivity(id, 'failed')
      throw cause
    }
    finally {
      busy.value = false
      if (type === 'campaign') {
        const next = { ...campaignSending.value }
        delete next[id]
        campaignSending.value = next
      }
    }
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
    setActivity(conversationId, 'generating')
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
            artifacts: await hydrateArtifacts(
              workflow,
              await request<GenerationArtifact[]>(`${base}/artifacts`),
            ),
          })
          setActivity(conversationId, 'completed')
          return
        }
        if (job.status === 'failed' || job.status === 'dead') {
          throw new Error(await failureReason(base, job))
        }
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS))
      }
      throw new Error(
        'Generation exceeded the backend processing window. Reopen this chat to refresh its status.',
      )
    }
    catch (cause) {
      patchProgress(conversationId, { running: false, error: messageOf(cause, 'Generation failed.') })
      setActivity(conversationId, 'failed')
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
      return typeof stage === 'string' && stage in POST_STAGE_TO_PROGRESS ? stage : fallback
    }
    catch {
      // Workflow state is initialized asynchronously; job status is authoritative.
      return fallback
    }
  }

  async function hydrateArtifacts(
    workflow: ChatWorkflow,
    artifacts: GenerationArtifact[],
  ): Promise<GenerationArtifact[]> {
    if (!import.meta.client) return artifacts
    return await Promise.all(artifacts.map(async (artifact) => {
      if (!artifact.mime_type.startsWith('image/')) return artifact
      const url = `${api}/posts/${workflow.post_id}/generations/${workflow.generation_id}/artifacts/${artifact.id}/content`
      const response = await fetch(url, { headers: headers() })
      if (!response.ok) return artifact
      return { ...artifact, preview_url: URL.createObjectURL(await response.blob()) }
    }))
  }

  function setProgress(conversationId: string, value: ChatProgress) {
    progress.value = { ...progress.value, [conversationId]: value }
  }

  function patchProgress(conversationId: string, value: Partial<ChatProgress>) {
    const current = progress.value[conversationId]
    if (!current) return
    progress.value = { ...progress.value, [conversationId]: { ...current, ...value } }
  }

  function setActivity(conversationId: string, value: ChatActivityState) {
    activity.value = { ...activity.value, [conversationId]: value }
  }

  async function uploadAttachments(messageId: string) {
    for (const item of attachments.value) {
      const body = new FormData()
      body.append('message_id', messageId)
      // The Asset Intelligence stage determines the precise semantic role.
      // Uploads enter through the valid neutral role accepted by the API.
      body.append('role', 'supporting_asset')
      body.append('file', await normalizeImageMimeType(item.file))
      await request(`${api}/assets`, { method: 'POST', body })
    }
    clearAttachments()
  }

  async function normalizeImageMimeType(file: File): Promise<File> {
    const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer())
    let detected: string | null = null
    if (bytes.length >= 3 && bytes[0] === 0xFF && bytes[1] === 0xD8 && bytes[2] === 0xFF) {
      detected = 'image/jpeg'
    }
    else if (
      bytes.length >= 8
      && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4E && bytes[3] === 0x47
      && bytes[4] === 0x0D && bytes[5] === 0x0A && bytes[6] === 0x1A && bytes[7] === 0x0A
    ) {
      detected = 'image/png'
    }
    else if (
      bytes.length >= 12
      && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF'
      && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP'
    ) {
      detected = 'image/webp'
    }
    if (!detected || detected === file.type.toLowerCase()) return file
    return new File([file], file.name, { type: detected, lastModified: file.lastModified })
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
    campaignStates,
    progress,
    activity,
    attachments,
    busy,
    error,
    loadConversations,
    createConversation,
    open,
    send,
    addFiles,
    removeAttachment,
    clearAttachments,
  }
}
