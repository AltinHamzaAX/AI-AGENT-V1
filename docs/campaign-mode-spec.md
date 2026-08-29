# Campaign Mode Specification

## Status

- Final Requirements: Completed
- Technical Architecture: In Progress
- Schemas: Not Started
- State Machine: Not Started
- Tickets: Not Started
- Codex Prompts: Not Started
- Implementation: Not Started

---

# 1. Final Requirements — Campaign Mode V1

## 1.1 Purpose

Campaign Mode is part of the Promotiva chat experience and is responsible for helping a business plan a marketing campaign in a structured and professional way.

Campaign Mode must not behave only as a general chatbot. It must understand the user's campaign request, collect the necessary context, build a Campaign Brief, and generate a structured Campaign Plan when enough information is available.

---

## 1.2 Main User Flow

The main flow is:

User message → Understand request → Extract campaign information → Update Campaign Brief → Check missing information → Ask follow-up question when needed → Generate Campaign Plan → Validate Campaign Plan → Save Campaign Plan → Display Campaign Plan → Allow Campaign Export

The system must preserve campaign progress across the conversation.

---

## 1.3 Campaign Brief Requirements

Campaign Mode must build a Campaign Brief gradually from information provided naturally during the conversation.

The Campaign Brief must support at least these fields:

- Business
- Product or Service
- Campaign Goal
- Target Audience
- Location
- Offer
- Channels
- Budget
- Duration

The system must be able to extract multiple fields from one user message.

Example:

> I have a gym in Prishtina and want to attract more students.

The system should be able to infer at least:

- Business: Gym
- Location: Prishtina
- Goal: Acquire new customers
- Audience: Students

The system must not ask again for information that the user has already provided.

The Campaign Brief must support corrections and updates.

Example:

> Actually my budget is €300, not €200.

The current confirmed budget must be updated from €200 to €300.

When a user gives uncertain information, the system should preserve that uncertainty or ask for clarification when the uncertainty materially affects the plan.

Example:

> My budget might be around €200–300.

The system must not silently convert that into a confirmed €200 budget.

---

## 1.4 Required, Contextual, and Optional Fields

Campaign Brief fields must not all be treated as equally mandatory.

### Required

- Business or Product/Service
- Campaign Goal
- Target Audience

### Contextually Required

- Location
- Duration

These may be required depending on the campaign type and scope.

### Optional / Recommended

- Offer
- Channels
- Budget

If the user does not know an optional field, the system should not block progress unnecessarily. It may provide a recommendation and clearly label it as an AI recommendation.

---

## 1.5 Facts vs Recommendations

Campaign Mode must distinguish between:

- facts explicitly provided by the user;
- inferred context;
- AI-generated recommendations or assumptions.

Example:

User-provided fact:

> Budget: €200

AI recommendation:

> Recommended budget: €500

The system must not present AI recommendations as if they were facts supplied by the user.

---

## 1.6 Conversation Requirements

The conversation must feel natural and human.

Campaign Mode must not behave like a static form.

The backend should determine what information is still needed, while the LLM may phrase the next question naturally according to the business context.

The system should avoid presenting a long questionnaire in one message when the information can be collected naturally over multiple turns.

If the user does not know an answer, the system should not block the campaign flow unnecessarily.

The system should answer in the user's current conversation language unless the user requests another language.

---

## 1.7 Out-of-Domain Messages

If the user asks a simple harmless question outside Campaign Mode's main purpose, the system may answer briefly and then guide the user back to campaign planning.

Example:

> User: How much is 2 + 2?

Possible response:

> 2 + 2 = 4. I can also help you plan a marketing campaign. What would you like to promote?

Campaign Mode must not lose its primary role as a marketing campaign assistant.

---

## 1.8 Campaign Readiness

Campaign Mode must determine when enough context exists to generate the Campaign Plan.

The system must not require every possible field to be filled before continuing.

It should stop asking questions when enough relevant information is available.

When the campaign is ready, the system transitions from briefing to plan generation.

---

## 1.9 Campaign Plan Requirements

The Campaign Plan must be structured and must not exist only as one long free-form text response.

It must contain at least:

1. Campaign Name
2. Campaign Summary
3. Campaign Objective
4. Target Audience
5. Offer / Value Proposition
6. Key Message
7. Campaign Strategy
8. Channels
9. Content Direction
10. Budget Allocation
11. Timeline
12. KPIs
13. Recommended Next Steps

The Campaign Plan must use the Campaign Brief as its primary campaign context.

The system must not invent concrete business facts and present them as user-provided information.

Recommendations must be distinguishable from confirmed facts.

---

## 1.10 Campaign Plan Quality Requirements

A valid Campaign Plan should:

- align with the campaign goal;
- use the target audience meaningfully;
- respect the location when relevant;
- respect the confirmed budget when one exists;
- respect the confirmed duration;
- not contradict the Campaign Brief;
- include relevant KPIs;
- include practical next steps;
- clearly distinguish recommendations from confirmed user data.

The goal is to produce a practical professional baseline comparable to basic campaign planning work performed by a marketing agency.

Advanced research-based agency capabilities are outside the initial V1 scope.

---

## 1.11 Campaign Plan Output and Validation

The LLM must return the Campaign Plan in a structured format that the backend can validate and the frontend can render using separate UI components.

The backend must validate LLM output before saving or presenting it as a completed plan.

The system must not blindly trust malformed, incomplete, or invalid model output.

If validation fails, the system should retry, repair, or use a controlled fallback.

---

## 1.12 Campaign State

Campaign Mode must maintain the current state of the campaign process.

The system must know:

- what campaign information has already been collected;
- what information is still missing;
- the current stage of the campaign;
- whether a Campaign Plan has been generated.

The detailed State Machine will be defined separately, but the process must support at least:

Discovery / Briefing → Ready → Generating → Plan Ready

`PLAN_READY` is a state, not the Campaign Plan itself.

After `PLAN_READY`, the user must still be able to correct campaign information or request changes.

If campaign information changes after the plan has been generated, the system should update the Campaign Brief and regenerate or update the Campaign Plan as appropriate.

---

## 1.13 LLM Requirements

The initial implementation will be designed for Gemini API.

Campaign Mode must not be tightly coupled to Gemini.

There must be an abstraction layer between Campaign Mode and the concrete LLM provider.

Conceptually:

Campaign Mode → LLM Service → Gemini Provider

The architecture should allow Gemini to be replaced or supplemented later by Groq, OpenAI, or another provider without rewriting the entire Campaign Mode module.

API credentials must never be exposed in the frontend or hard-coded in source code.

---

## 1.14 Persistence Requirements

Campaign Brief, Campaign Plan, and the state needed to resume campaign work must be persisted.

The user should not lose campaign progress simply because the page is refreshed or the conversation is reopened.

The exact database model will be defined during Technical Architecture and Data Architecture.

Each Campaign Plan must belong to a specific Campaign.

Conceptually:

Campaign
- Campaign Brief
- Campaign Plan

---

## 1.15 Error Handling Requirements

Failures must be handled gracefully.

The system must account for errors such as:

- LLM provider unavailable;
- API rate limit reached;
- request timeout;
- invalid LLM response;
- invalid structured output;
- persistence/database failure;
- export failure.

Whenever possible, a technical failure must not destroy an already collected Campaign Brief.

Users should receive clear, non-technical error messages where appropriate.

---

## 1.16 Campaign Export Requirements

Campaign Mode must support exporting the generated campaign as a `.zip` file.

The ZIP is a Campaign Package, not the Campaign Plan itself.

The minimum V1 Campaign Package must contain:

```text
campaign-name.zip
├── campaign-plan.pdf
├── campaign-plan.json
└── campaign-brief.json
```

Future versions may additionally include:

- posts;
- copy;
- images;
- creative assets;
- research;
- additional campaign files.

---

## 1.17 Scope of V1

Campaign Mode V1 must:

- understand the user's campaign request;
- build the Campaign Brief;
- maintain campaign state;
- identify missing information;
- ask context-aware follow-up questions;
- generate a structured Campaign Plan;
- validate the Campaign Plan;
- save campaign progress and results;
- allow corrections after plan generation;
- support Campaign Package ZIP export;
- handle failures gracefully.

---

## 1.18 Out of Scope for Initial V1

The following are not required in the initial V1:

- Multi-agent system
- Autonomous research agents
- Real-time market research
- Full competitor intelligence
- Social-media crawling
- Automatic campaign publishing
- Automated ad buying
- Advanced campaign analytics
- Automatic campaign optimization
- Designer Agent
- Copywriter Agent
- Critic Agent

The architecture should not unnecessarily block these capabilities from being added later.

---

## 1.19 Primary Backend Scope

Campaign-specific backend logic should be concentrated primarily inside:

`backend/app/modules/campaigns`

Changes outside this module should be limited to necessary integration points and should remain minimal.

---

## 1.20 Success Definition

Campaign Mode V1 is considered functionally successful when a user can start with an incomplete request such as:

> I have a gym in Prishtina and want more customers.

and the system can:

1. understand the information already provided;
2. gradually build the Campaign Brief;
3. ask relevant follow-up questions without unnecessary repetition;
4. distinguish confirmed facts from recommendations;
5. determine when enough information exists;
6. generate a structured Campaign Plan;
7. validate the generated plan;
8. save the Campaign Brief and Campaign Plan;
9. allow later corrections and regeneration;
10. render the plan in a frontend-friendly structured format;
11. export the campaign as a `.zip` Campaign Package;
12. handle expected failures without losing campaign progress.

---

# 2. Technical Architecture

**Status: Completed**

## Architecture Diagram

![Campaign Mode Architecture](./diagrams/campaign-mode-architecture.png)

This section defines how the system will technically satisfy the Final Requirements.

Planned architecture areas:

- High-Level Design
- Components
- Data Architecture
- API Design
- LLM Architecture
- Persistence
- Error Handling
- Security
- Export Architecture

2.1 High-Level Design

Campaign Mode will follow this high-level flow:

User
↓
Nuxt Chat UI
↓
FastAPI Backend
↓
Campaign Router
↓
Campaign Service / Orchestrator
↓
Campaign Components
├── Extractor
├── Validator
├── State Logic
├── Campaign Generator
├── Repository
├── LLM Service
└── Export Service
↓
PostgreSQL / LLM Provider

The Campaign Service acts as the central orchestrator and coordinates campaign-specific operations without implementing every responsibility itself.

2.2 Main Components

The Campaign Mode backend should contain clear responsibilities for:

Campaign Router
Campaign Service / Orchestrator
Campaign Extractor
Campaign Validator
Campaign State Logic
Campaign Generator
Campaign Plan Validator
Campaign Repository
LLM Service
LLM Provider Interface
Gemini Provider
Prompt definitions
Campaign Export Service

A logical component does not necessarily correspond to exactly one physical file.

Campaign-specific backend logic should remain primarily inside:

backend/app/modules/campaigns
2.3 Data Architecture

The V1 domain model is:

Conversation
├── many Messages
└── one Campaign

Campaign
├── one Campaign Brief
├── zero or one current Campaign Plan
└── Campaign Status

Main persistence model:

campaigns
campaign_briefs
campaign_plans

Campaign status remains part of the Campaign rather than a separate table.

Main states include:

BRIEFING
READY
GENERATING
PLAN_READY

Campaign Plan version history is outside the initial V1 scope.

Campaign Brief fields may be nullable in the database while information is still being collected. Business-level readiness rules determine which fields must exist before Campaign Plan generation.

2.4 API Design

V1 API Design:

POST /campaigns
POST /campaigns/{id}/messages
GET  /campaigns/{id}
POST /campaigns/{id}/generate
GET  /campaigns/{id}/plan
GET  /campaigns/{id}/export

Brief corrections made through the conversation should use the message endpoint.

A separate PATCH /brief endpoint should only be introduced if the frontend later provides a direct Campaign Brief editor.

The same /generate endpoint should support initial generation and regeneration.

Campaign Plan generation should not happen automatically when the Campaign becomes READY. The user initiates generation explicitly.

2.5 LLM Architecture

Campaign components must not call Gemini directly.

The architecture should be:

Campaign Components
↓
LLM Service
↓
LLM Provider Interface
↓
Gemini Provider
↓
Gemini API
↓
Gemini Model

The LLM Service provides a provider-independent interface.

The initial provider is Gemini, while the architecture should allow future providers such as Groq or OpenAI.

Primary V1 LLM capabilities:

1. Campaign information extraction
2. Natural conversational responses / follow-up questions
3. Structured Campaign Plan generation

For normal campaign messages, V1 should aim for one LLM call returning:

extracted_fields
+
natural reply

Campaign Plan generation uses a separate LLM call.

LLM outputs should use structured outputs and must be validated before being persisted.

Two main prompt responsibilities should be separated:

Conversation / Extraction Prompt
Campaign Plan Generation Prompt
2.6 Persistence

Campaign data should be persisted in PostgreSQL.

The system should persist:

Campaign
Campaign Brief
Campaign Status
Current Campaign Plan

Campaign Brief should be updated gradually whenever useful new information is extracted.

Campaign Plan should only be persisted after successful schema validation.

When Campaign Brief changes after a plan has already been generated, the existing plan should be treated as outdated and require explicit regeneration.

Automatic regeneration is not required in V1.

Conversation and Message persistence should reuse the application's existing chat infrastructure if available.

2.7 Error Handling

Campaign Mode should classify known errors and return clear user-friendly responses.

Relevant cases include:

LLM rate limit
LLM timeout
Provider unavailable
Invalid structured output
Campaign not ready
Campaign not found
Database failure
Export failure

Technical errors should not expose secrets or sensitive internal details.

If Campaign Plan generation fails:

Campaign remains saved
Campaign Brief remains saved
Invalid Plan is not persisted
Campaign must not remain stuck in GENERATING
User can retry

Limited retry may be used for appropriate temporary failures.

2.8 Security

Secrets such as API keys and database credentials must remain server-side and must not be committed to source control.

Environment variables should be used for sensitive configuration.

Campaign Mode should reuse existing application authentication and authorization mechanisms when available.

Backend authorization must verify that the current user has access to the requested Campaign.

User input must be validated.

Internal stack traces, API keys, passwords and other sensitive values must not be exposed in API responses or logs.

System prompts should be managed on the backend.

The LLM must not be trusted to make security-sensitive decisions such as authorization or direct database access.

2.9 Export Architecture

Campaign Mode V1 must support Campaign Package export as .zip.

Minimum package:

campaign-name.zip
├── campaign-plan.pdf
├── campaign-plan.json
└── campaign-brief.json

Export flow:

User requests Export
↓
Campaign Export Service
↓
Load saved Campaign Brief + Campaign Plan
↓
Generate PDF
↓
Generate JSON files
↓
Create ZIP package
↓
Return ZIP through API
↓
User saves the package

Export requires a valid Campaign Plan.

Generated ZIP packages do not need permanent database storage in V1. They can be generated on demand from persisted campaign data.

Export failures must not modify or destroy existing Campaign, Brief or Plan data.
