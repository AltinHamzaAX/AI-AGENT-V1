export type ConversationType = 'post' | 'campaign'

export interface Conversation { id: string; project_id: string; title: string | null; type: ConversationType; created_at: string; updated_at: string }

export type ChatIntent = 'GENERAL_CONVERSATION' | 'MARKETING_QUESTION' | 'MISSING_INFORMATION' | 'GENERATE_POST' | 'REVISE_POST' | 'CLARIFICATION'
export type ChatAction = 'reply' | 'ask' | 'generate' | 'revise'

export interface ChatTurnMetadata { intent?: ChatIntent; action?: ChatAction; reason?: string; questions?: string[]; generation_ready?: boolean; post_id?: string; generation_id?: string; attempt?: number; revises_generation_id?: string }
export interface ChatMessage { id: string; conversation_id: string; sequence: number; role: 'user' | 'assistant' | 'system' | 'tool'; content: string; metadata: { chat?: ChatTurnMetadata } & Record<string, unknown>; created_at: string }

export type CampaignStatus = 'BRIEFING' | 'READY' | 'GENERATING' | 'PLAN_READY'
export interface CampaignBrief {
  business: string | null
  product_or_service: string | null
  goal: string | null
  audience: string | null
  location: string | null
  offer: string | null
  value_proposition: string | null
  channels: string[] | null
  budget_amount: number | null
  budget_currency: string | null
  duration: string | null
  brand_tone: string | null
  constraints: string[] | null
}
export interface CampaignDetail {
  id: string
  conversation_id: string
  status: CampaignStatus
  brief: CampaignBrief
  plan_available: boolean
  created_at: string
  updated_at: string
}
export interface CampaignCreate { id: string; conversation_id: string; status: CampaignStatus }
export interface CampaignMessageResponse { reply: string; status: CampaignStatus; brief: CampaignBrief }

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
  generation_ready: boolean
}

export interface ChatProgress { running: boolean; stage: string; result: boolean; error: string; artifacts: GenerationArtifact[] }
export type ChatActivityState = 'idle' | 'sending' | 'thinking' | 'responding' | 'generating' | 'completed' | 'failed'
export interface ChatWorkflow { post_id: string; generation_id: string; attempt: number; deduplicated: boolean; revises_generation_id: string | null }
export interface ChatTurn { user: ChatMessage; assistant: ChatMessage; intent: ChatIntent; action: ChatAction; questions: string[]; workflow: ChatWorkflow | null; context: ConversationContext; generation_ready: boolean }
export interface ChatState { context: ConversationContext; post_id: string | null; generation: PostGeneration | null; artifacts: GenerationArtifact[] }

export type GenerationJobStatus = 'queued' | 'running' | 'retry_scheduled' | 'completed' | 'failed' | 'dead'
export interface PostGeneration { id: string; post_id: string; attempt: number; status: string; job_status: GenerationJobStatus }
export interface GenerationJob { status: GenerationJobStatus; last_error_code: string | null }
export interface GenerationArtifact { id: string; kind: 'intermediate' | 'preview' | 'final'; mime_type: string; width: number | null; height: number | null; metadata: Record<string, unknown>; preview_url?: string }

export const POST_PROGRESS = [['understanding', 'Understanding your request'], ['brand', 'Analyzing brand'], ['research', 'Researching'], ['strategy', 'Building strategy'], ['concept', 'Creating concept'], ['design', 'Designing'], ['generation', 'Generating'], ['review', 'Reviewing'], ['finalizing', 'Finalizing']] as const

export const POST_STAGE_TO_PROGRESS: Record<string, typeof POST_PROGRESS[number][0]> = {
  client_understanding: 'understanding',
  semantic_contract: 'understanding',
  asset_intelligence: 'brand',
  brand_product: 'brand',
  audience_intelligence: 'research',
  external_research: 'research',
  marketing_strategy: 'strategy',
  creative_concept: 'concept',
  copywriting: 'concept',
  art_direction: 'design',
  design_spec: 'design',
  reference_validation: 'design',
  generation_planning: 'design',
  production: 'generation',
  scene_purity: 'generation',
  composition: 'generation',
  verification: 'review',
  quality_review: 'review',
  design_review: 'review',
  vision_review: 'review',
  quality_scoring: 'finalizing',
}

export function postProgressIndex(stage?: string): number {
  const phase = stage
    ? POST_STAGE_TO_PROGRESS[stage] || POST_PROGRESS.find(item => item[0] === stage)?.[0]
    : undefined
  return POST_PROGRESS.findIndex(item => item[0] === phase)
}

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
