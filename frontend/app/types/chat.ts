export type ConversationType = 'post' | 'campaign'

export interface Conversation { id: string; project_id: string; title: string | null; type: ConversationType; created_at: string; updated_at: string }

export type ChatIntent = 'GENERAL_CONVERSATION' | 'MARKETING_QUESTION' | 'MISSING_INFORMATION' | 'GENERATE_POST' | 'REVISE_POST' | 'CLARIFICATION'
export type ChatAction = 'reply' | 'ask' | 'generate' | 'revise'

export interface ChatTurnMetadata { intent?: ChatIntent; action?: ChatAction; reason?: string; questions?: string[]; post_id?: string; generation_id?: string; attempt?: number; revises_generation_id?: string }
export interface ChatMessage { id: string; conversation_id: string; sequence: number; role: 'user' | 'assistant' | 'system' | 'tool'; content: string; metadata: { chat?: ChatTurnMetadata } & Record<string, unknown>; created_at: string }

export interface PendingAttachment { id: string; file: File; previewUrl: string }
export interface ContextAsset { id: string; message_id: string; role: string; original_filename: string; mime_type: string; width: number | null; height: number | null }

export interface ConversationContext {
  business: string | null
  brand: string | null
  product_service: string | null
  goal: string | null
  audience: string | null
  market: string | null
  location: string | null
  platform: string | null
  language: string | null
  offer: string | null
  cta_intent: string | null
  style_preferences: string[]
  constraints: string[]
  attachments: ContextAsset[]
  missing_fields: string[]
  generated_posts: { post_id: string; generation_id: string; attempt: number; revises_generation_id: string | null; instruction: string | null }[]
  revision_instructions: string[]
}

export interface ChatProgress { running: boolean; stage: string; result: boolean; error: string; artifacts: GenerationArtifact[] }
export interface ChatWorkflow { post_id: string; generation_id: string; attempt: number; deduplicated: boolean; revises_generation_id: string | null }
export interface ChatTurn { user: ChatMessage; assistant: ChatMessage; intent: ChatIntent; action: ChatAction; questions: string[]; workflow: ChatWorkflow | null; context: ConversationContext }
export interface ChatState { context: ConversationContext; post_id: string | null; generation: PostGeneration | null; artifacts: GenerationArtifact[] }

export type GenerationJobStatus = 'queued' | 'running' | 'retry_scheduled' | 'completed' | 'failed' | 'dead'
export interface PostGeneration { id: string; post_id: string; attempt: number; status: string; job_status: GenerationJobStatus }
export interface GenerationJob { status: GenerationJobStatus; last_error_code: string | null }
export interface GenerationArtifact { id: string; kind: 'intermediate' | 'preview' | 'final'; mime_type: string; width: number | null; height: number | null; metadata: Record<string, unknown> }

export const POST_PROGRESS = [['client_understanding', 'Understanding your request'], ['brand_product', 'Analyzing brand'], ['external_research', 'Researching'], ['marketing_strategy', 'Building strategy'], ['creative_concept', 'Creating concept'], ['design_spec', 'Designing'], ['production', 'Generating'], ['design_review', 'Reviewing'], ['quality_scoring', 'Finalizing']] as const

export const INTENT_LABELS: Record<ChatIntent, string> = {
  GENERAL_CONVERSATION: 'Conversation',
  MARKETING_QUESTION: 'Marketing advice',
  MISSING_INFORMATION: 'Needs details',
  CLARIFICATION: 'Understood',
  GENERATE_POST: 'Generating post',
  REVISE_POST: 'Revising post',
}

export const CONTEXT_LABELS: [keyof ConversationContext, string][] = [
  ['business', 'Business'],
  ['brand', 'Brand'],
  ['product_service', 'Product'],
  ['goal', 'Goal'],
  ['audience', 'Audience'],
  ['platform', 'Platform'],
  ['offer', 'Offer'],
  ['location', 'Location'],
]
