export type ConversationType = 'post' | 'campaign'
export interface Conversation { id: string; project_id: string; title: string | null; type: ConversationType; created_at: string; updated_at: string }
export interface ChatMessage { id: string; conversation_id: string; sequence: number; role: 'user' | 'assistant' | 'system' | 'tool'; content: string; metadata: Record<string, unknown>; created_at: string }
export interface PendingAttachment { id: string; file: File; previewUrl: string }
export interface PostGeneration { id: string; post_id: string; job_status: GenerationJobStatus }
export interface GenerationJob { status: GenerationJobStatus; last_error_code: string | null }
export interface GenerationArtifact { id: string; kind: 'intermediate' | 'preview' | 'final'; mime_type: string; width: number | null; height: number | null; metadata: Record<string, unknown> }
export type GenerationJobStatus = 'queued' | 'running' | 'retry_scheduled' | 'completed' | 'failed' | 'dead'
export const POST_PROGRESS = [['client_understanding', 'Understanding your request'], ['brand_product', 'Analyzing brand'], ['external_research', 'Researching'], ['marketing_strategy', 'Building strategy'], ['creative_concept', 'Creating concept'], ['design_spec', 'Designing'], ['production', 'Generating'], ['design_review', 'Reviewing'], ['quality_scoring', 'Finalizing']] as const
