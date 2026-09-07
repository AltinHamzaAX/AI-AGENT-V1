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

# 3. Schemas

**Status: Completed**

This section defines the structured data contracts used by Campaign Mode.

## 3.1 Campaign Brief Schema

The Campaign Brief represents the structured campaign context collected gradually from the user's conversation.

Fields:

- `business`: string | null
- `product_or_service`: string | null
- `goal`: string | null
- `audience`: string | null
- `location`: string | null
- `offer`: string | null
- `value_proposition`: string | null
- `channels`: list[string] | null
- `budget_amount`: number | null
- `budget_currency`: string | null
- `duration`: string | null
- `brand_tone`: string | null
- `constraints`: list[string] | null

Campaign Brief fields may remain null while information is being collected.

Readiness should not require every field to be populated.

Required before generation:

- `business` or `product_or_service`
- `goal`
- `audience`

Contextually important:

- `location`
- `duration`

Optional or recommended:

- `offer`
- `value_proposition`
- `channels`
- `budget_amount`
- `budget_currency`
- `brand_tone`
- `constraints`

Unknown optional information must not block Campaign Plan generation.

AI recommendations must not be presented as confirmed business facts.

## 3.2 Campaign Plan Schema

The Campaign Plan is the structured strategic output generated from a sufficiently complete Campaign Brief.

Main fields:

- `campaign_name`: string
- `executive_summary`: string
- `objective`: Objective
- `target_audience`: TargetAudience
- `offer`: string | null
- `value_proposition`: string
- `positioning`: string
- `key_message`: string
- `strategy`: string
- `channels`: list[ChannelStrategy]
- `content_direction`: list[ContentDirection]
- `budget_allocation`: BudgetAllocation | null
- `timeline`: list[TimelinePhase]
- `kpis`: list[KPI]
- `assumptions_or_risks`: list[string]
- `next_steps`: list[string]

Supporting nested structures:

### Objective

- `primary`: string
- `secondary`: string | null

`primary` is required and must not be empty.

### TargetAudience

- `primary`: string
- `location`: string | null
- `needs_or_motivations`: list[string]

`primary` is required and must not be empty.

### ChannelStrategy

- `name`: string
- `purpose`: string
- `reason`: string

### ContentDirection

- `idea`: string
- `purpose`: string

### BudgetAllocation

- `total`: number
- `currency`: string
- `items`: list[BudgetItem]

### BudgetItem

- `channel`: string
- `amount`: number
- `reason`: string

### TimelinePhase

- `period`: string
- `phase`: string
- `objective`: string
- `activities`: list[string]

### KPI

- `name`: string
- `purpose`: string

The Campaign Plan should distinguish confirmed campaign facts from AI-generated strategic recommendations.

The timeline must reflect the campaign duration defined in the Campaign Brief.

Budget allocation must respect the confirmed campaign budget when one is provided.

`content_direction` defines recommended content direction for the campaign. Direct integration with Posts Mode is outside the Campaign Mode V1 scope.

## 3.3 API Request and Response Schemas

Primary V1 API schemas include:

### CreateCampaignRequest

- `conversation_id`

### CreateCampaignResponse

- `id`
- `conversation_id`
- `status`

### CampaignMessageRequest

- `message`: string

### CampaignMessageResponse

- `reply`
- `status`
- `brief`: CampaignBrief

### CampaignDetailResponse

- `id`
- `conversation_id`
- `status`
- `brief`: CampaignBrief
- `plan_available`

### GenerateCampaignResponse

- `status`
- `plan`: CampaignPlan

### CampaignPlanResponse

Returns the current structured Campaign Plan.

### ExportResponse

The export endpoint returns a ZIP file rather than a JSON response.

## 3.4 Error Schema

Campaign Mode should use a consistent error response structure.

Example:

{
  "error": {
    "code": "CAMPAIGN_NOT_READY",
    "message": "More campaign information is required before generating the plan."
  }
}

The error code is intended for programmatic handling, while the message provides a user-friendly explanation.

# 4. Flow / State Machine

**Status: Completed**

This section defines the Campaign Mode workflow and the rules governing Campaign state transitions.

## 4.1 Campaign States

Campaign Mode uses the following primary states:

- `BRIEFING`
- `READY`
- `GENERATING`
- `PLAN_READY`

### BRIEFING

The Campaign Brief is still being collected or does not yet contain enough information for Campaign Plan generation.

### READY

The Campaign Brief contains sufficient information to generate a Campaign Plan.

A READY campaign does not yet imply that a Campaign Plan exists.

### GENERATING

Campaign Plan generation is currently in progress.

### PLAN_READY

A valid Campaign Plan has been generated, validated and persisted.

## 4.2 Main Campaign Flow

The normal Campaign Mode flow is:

BRIEFING
↓
READY
↓
GENERATING
↓
PLAN_READY

The transition from `BRIEFING` to `READY` occurs when Campaign Brief readiness rules are satisfied.

Campaign Plan generation is initiated explicitly by the user.

## 4.3 State Transition Rules

| Current State | Event | Next State |
|---|---|---|
| BRIEFING | Brief remains insufficient | BRIEFING |
| BRIEFING | Brief becomes sufficient | READY |
| READY | Brief becomes insufficient | BRIEFING |
| READY | User requests generation | GENERATING |
| GENERATING | Valid plan generated and saved | PLAN_READY |
| GENERATING | Generation fails | READY |
| PLAN_READY | Campaign Brief changes | READY |
| PLAN_READY | Campaign is exported | PLAN_READY |

## 4.4 Generation Failure

If Campaign Plan generation fails:

- the Campaign Brief remains persisted;
- an invalid Campaign Plan must not be saved;
- the Campaign must not remain stuck in `GENERATING`;
- the Campaign returns to `READY`;
- the user may retry generation.

## 4.5 Brief Changes After Plan Generation

If the Campaign Brief changes after reaching `PLAN_READY`, the existing Campaign Plan should be considered outdated.

For V1, any Campaign Brief change after plan generation returns the Campaign to `READY`.

The system should not automatically regenerate the plan.

The user explicitly requests regeneration through the same Campaign Plan generation endpoint.

## 4.6 Export Behavior

Export does not change Campaign state.

A Campaign in `PLAN_READY` remains in `PLAN_READY` after a successful export.

Export uses the already persisted Campaign Brief and Campaign Plan and does not trigger a new LLM generation.

# 5. LLM Integration Details

**Status: Completed**

This section defines how Campaign Mode integrates with the configured Large Language Model provider.

## 5.1 Provider Architecture

Campaign components must not call Gemini directly.

The integration flow is:

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

Gemini is the initial provider for V1, while the architecture should allow future provider implementations such as Groq or OpenAI.

## 5.2 LLM Configuration

LLM configuration should be provided through backend environment variables.

Example configuration:

```text
LLM_PROVIDER=gemini
LLM_MODEL=<configured-model>
GEMINI_API_KEY=<secret>

API keys and other provider credentials must never be exposed to the frontend or committed to source control.

The concrete Gemini model should be configurable rather than hard-coded throughout Campaign Mode.

5.3 Conversation and Extraction Call

For a normal user message, V1 should aim to use one LLM call for both:

campaign information extraction;
natural conversational response generation.

Input context should include:

conversation/extraction system instructions;
current Campaign Brief;
latest user message;
required structured output schema.

Expected structured output:

{
  "reply": "Natural conversational response",
  "extracted_fields": {
    "business": null,
    "product_or_service": null,
    "goal": null,
    "audience": null,
    "location": null,
    "offer": null,
    "value_proposition": null,
    "channels": null,
    "budget_amount": null,
    "budget_currency": null,
    "duration": null,
    "brand_tone": null,
    "constraints": null
  }
}

The backend validates the structured output before updating the Campaign Brief.

Existing confirmed information should not be overwritten unless the user clearly provides a correction.

5.4 Follow-Up Questions

Campaign readiness and missing-field logic remain backend responsibilities.

The backend determines what information is still needed.

The LLM is responsible for phrasing the next useful question naturally and according to the current Campaign Brief.

Conceptually:

Backend determines WHAT is needed
              ↓
LLM determines HOW to ask naturally

The system should avoid repeating questions for information already known.

Unknown optional fields such as brand_tone or constraints must not block Campaign Plan generation when the Campaign Brief otherwise contains sufficient information.

5.5 Campaign Plan Generation

Campaign Plan generation uses a separate LLM call.

The generation input includes:

marketing strategist system instructions;
confirmed Campaign Brief;
Campaign Plan Schema;
Campaign Plan quality rules.

Conceptually:

System Instructions
        +
Campaign Brief
        +
Campaign Plan Schema
        +
Quality Rules
        ↓
       LLM
        ↓
Structured Campaign Plan

The generated plan must respect confirmed campaign facts, campaign duration and confirmed budget when provided.

AI recommendations must not be presented as confirmed business facts.

The model must not invent missing business information merely to complete the Campaign Plan.

When appropriate, missing optional information may result in clearly identified strategic recommendations.

5.6 Structured Output Validation

LLM output must not be trusted without validation.

The validation flow is:

LLM Response
      ↓
Schema Validation
      ↓
   Valid?
   /    \
 YES     NO
  ↓       ↓
Continue  Controlled repair/retry
              ↓
          Still invalid?
              ↓
        Controlled error

Only valid structured output may continue through the Campaign workflow.

Invalid Campaign Plans must not be persisted.

5.7 Retry and Failure Handling

Retries should be limited and used only for appropriate temporary or recoverable failures.

Relevant cases include:

provider timeout;
provider temporarily unavailable;
invalid structured output.

A provider timeout occurs when the configured LLM provider does not return a response within the allowed request time.

A provider-unavailable error occurs when the external LLM service is temporarily unable to process the request.

A failed LLM call must not destroy persisted Campaign Brief data.

Campaign Plan generation failure must return the Campaign from GENERATING to READY.

Retry attempts must be controlled and limited. Infinite retry loops are not allowed.

If the provider remains unavailable or the response remains invalid after the allowed retry strategy, Campaign Mode should return a controlled and user-friendly error.

5.8 Backend Responsibilities

The LLM is responsible for language understanding, campaign information extraction, natural conversational responses and Campaign Plan generation.

The LLM must not control:

authentication;
authorization;
campaign ownership;
database access;
persistence;
Campaign state transitions;
readiness rules;
export;
security-sensitive decisions.

These responsibilities remain controlled by backend application logic.

Conceptually:

LLM
├── Understand language
├── Extract campaign information
├── Generate natural responses
└── Generate Campaign Plan

Backend
├── Validate
├── Decide readiness
├── Control state
├── Authorize access
├── Persist data
├── Handle errors
└── Export
5.9 Provider Switching

Campaign Mode should depend on an LLM Provider interface rather than directly on Gemini-specific code.

The initial implementation uses Gemini.

Future providers may be added through additional provider implementations without rewriting Campaign-specific business logic.

Example:

LLM Provider Interface
├── Gemini Provider
├── Groq Provider       (future)
└── OpenAI Provider     (future)

Provider selection should be configuration-driven.

Conceptually:

LLM_PROVIDER=gemini
        ↓
Gemini Provider

Future:

LLM_PROVIDER=groq
        ↓
Groq Provider

Campaign-specific components such as the Campaign Service, Extractor and Campaign Generator should remain independent of provider-specific API details.

5.10 V1 LLM Integration Summary

Campaign Mode uses two primary LLM flows.

Conversation / Extraction
Latest User Message
        +
Current Campaign Brief
        +
Conversation Instructions
        +
Structured Output Schema
        ↓
       LLM
        ↓
reply + extracted_fields
        ↓
Backend Validation
        ↓
Campaign Brief Update
        ↓
Persistence
Campaign Plan Generation
Confirmed Campaign Brief
        +
Marketing Strategist Instructions
        +
Campaign Plan Schema
        +
Quality Rules
        ↓
       LLM
        ↓
Structured Campaign Plan
        ↓
Plan Validation
        ↓
Persistence
        ↓
PLAN_READY

<!-- =============================== -->

# 6. Export Details

**Status: Completed**

This section defines how Campaign Mode exports an existing Campaign Plan and its supporting Campaign Brief.

The V1 export is designed to provide both a human-readable campaign document and structured machine-readable campaign data.

## 6.1 Export Package

Campaign Mode should export a ZIP package containing:

```text
campaign-export.zip
│
├── campaign-plan.pdf
├── campaign-plan.json
└── campaign-brief.json
```

Each file has a different purpose:

- `campaign-plan.pdf` provides a human-readable and professional representation of the Campaign Plan.
- `campaign-plan.json` provides the structured Campaign Plan data.
- `campaign-brief.json` provides the structured Campaign Brief used as the basis for the plan.

The ZIP package is the downloadable export artifact for Campaign Mode V1.

## 6.2 Export Source of Data

Export must use the Campaign Brief and Campaign Plan already persisted by the backend.

The export process must not call the LLM or generate a new Campaign Plan.

Conceptually:

```text
Saved Campaign
      +
Saved Campaign Brief
      +
Saved Campaign Plan
      ↓
Export Service
      ↓
Export Package
```

This ensures that the exported files represent the same Campaign Plan that the user sees in the application.

## 6.3 Export Flow

The V1 export flow is:

```text
User requests export
        ↓
GET /campaigns/{id}/export
        ↓
Authenticate and authorize request
        ↓
Load Campaign
        ↓
Load saved Campaign Brief
        ↓
Load saved Campaign Plan
        ↓
Validate export availability
        ↓
Generate campaign-brief.json
        ↓
Generate campaign-plan.json
        ↓
Render campaign-plan.pdf
        ↓
Create ZIP package
        ↓
Return ZIP file
```

Export generation is deterministic from the persisted Campaign data and does not require additional AI generation.

## 6.4 Campaign Plan PDF

`campaign-plan.pdf` is the human-readable representation of the Campaign Plan.

The PDF should present the plan in a clear and professional structure.

The document should include relevant Campaign Plan sections such as:

- campaign name;
- executive summary;
- objective;
- target audience;
- offer, when available;
- value proposition;
- positioning;
- key message;
- strategy;
- channel strategy;
- content direction;
- budget allocation, when available;
- timeline;
- KPIs;
- assumptions or risks;
- next steps.

The PDF is a presentation of the already generated Campaign Plan. It must not introduce new campaign facts or AI-generated strategy that does not exist in the persisted plan.

Exact visual styling and PDF rendering implementation are implementation-level decisions and may be finalized during the Export Service ticket.

## 6.5 Campaign Plan JSON

`campaign-plan.json` contains the persisted structured Campaign Plan.

Its structure should follow the Campaign Plan Schema defined in this specification.

Conceptually:

```json
{
  "campaign_name": "Student Fitness Boost",
  "executive_summary": "...",
  "objective": {
    "primary": "Acquire new student customers",
    "secondary": null
  },
  "target_audience": {
    "primary": "Students aged 18-25",
    "location": "Prishtina",
    "needs_or_motivations": [
      "Affordable gym access",
      "Flexible opening hours"
    ]
  },
  "offer": "...",
  "value_proposition": "...",
  "positioning": "...",
  "key_message": "...",
  "strategy": "...",
  "channels": [],
  "content_direction": [],
  "budget_allocation": {},
  "timeline": [],
  "kpis": [],
  "assumptions_or_risks": [],
  "next_steps": []
}
```

The exported JSON must represent the saved Campaign Plan rather than triggering regeneration.

## 6.6 Campaign Brief JSON

`campaign-brief.json` contains the Campaign Brief associated with the exported Campaign Plan.

Its structure should follow the Campaign Brief Schema defined in this specification.

Example:

```json
{
  "business": "FitZone Gym",
  "product_or_service": "Gym membership",
  "goal": "Acquire new customers",
  "audience": "Students aged 18-25",
  "location": "Prishtina",
  "offer": "50% off first month",
  "value_proposition": "Modern equipment, professional trainers and flexible opening hours",
  "channels": [
    "Instagram",
    "TikTok"
  ],
  "budget_amount": 200,
  "budget_currency": "EUR",
  "duration": "2 weeks",
  "brand_tone": "Energetic and motivating",
  "constraints": []
}
```

The Campaign Brief allows the exported package to preserve the context on which the Campaign Plan was based.

## 6.7 Export Availability

Export is available only when a valid Campaign Plan exists.

A Campaign without a generated plan must not produce an empty or incomplete ZIP package.

For example, an export request without an available Campaign Plan may return a controlled error:

```json
{
  "error": {
    "code": "CAMPAIGN_PLAN_NOT_AVAILABLE",
    "message": "Generate the Campaign Plan before exporting the campaign."
  }
}
```

The backend must verify Campaign access and plan availability before creating the export package.

## 6.8 Export and Campaign State

Export does not change Campaign state.

For example:

```text
PLAN_READY
    ↓
Export requested
    ↓
ZIP generated
    ↓
PLAN_READY
```

A successful export does not create a new Campaign Plan.

An export failure must also leave the Campaign and its persisted data unchanged.

## 6.9 ZIP Persistence

The generated ZIP package does not need to be permanently stored in V1.

The recommended V1 flow is:

```text
Persisted Brief + Plan
        ↓
Export requested
        ↓
Generate export files
        ↓
Create ZIP
        ↓
Return ZIP
        ↓
Clean temporary resources
```

The Campaign Brief and Campaign Plan remain the persistent source data.

If the user needs the package again, Campaign Mode can regenerate the export from the saved data.

## 6.10 Export Failure Handling

Relevant export failures may include:

- Campaign not found;
- user not authorized to access the Campaign;
- Campaign Plan not available;
- PDF rendering failure;
- JSON serialization failure;
- ZIP creation failure;
- unexpected storage or filesystem failure.

Export failures must:

- return a controlled error;
- not modify Campaign state;
- not delete or corrupt the Campaign Brief;
- not delete or corrupt the Campaign Plan;
- not expose internal stack traces or sensitive information.

Temporary export resources should be cleaned up when appropriate.

## 6.11 V1 Export Boundaries

V1 export includes:

```text
campaign-plan.pdf
campaign-plan.json
campaign-brief.json
```

The following are outside the required V1 export scope:

- generated social media posts;
- generated campaign images;
- videos;
- competitor research reports;
- market research datasets;
- advertising platform exports;
- analytics reports;
- automatically published assets.

These capabilities may be added in future versions without changing the core Campaign Plan export concept.

## 6.12 V1 Export Summary

The final V1 export flow is:

```text
Campaign Brief
       +
Campaign Plan
       ↓
Export Service
       ↓
Validate access and availability
       ↓
Generate:
├── campaign-plan.pdf
├── campaign-plan.json
└── campaign-brief.json
       ↓
Package as ZIP
       ↓
Return to user
```

No LLM call is required during export.

The persisted Campaign Brief and Campaign Plan remain the source of truth for exported campaign data.
