<script setup lang="ts">
import type { CampaignPlan } from '~/types/chat'

defineProps<{ plan: CampaignPlan, exporting: boolean }>()
defineEmits<{ export: [] }>()

function money(amount: number | string, currency: string) {
  return `${amount} ${currency}`
}
</script>

<template>
  <article class="campaign-plan">
    <header class="plan-hero">
      <div>
        <span>Campaign plan</span>
        <h2>{{ plan.campaign_name }}</h2>
        <p>{{ plan.executive_summary }}</p>
      </div>
      <button class="export-plan" :disabled="exporting" @click="$emit('export')">
        {{ exporting ? 'Preparing ZIP...' : 'Export ZIP' }}
      </button>
    </header>

    <div class="plan-grid">
      <section>
        <span class="section-number">01</span><h3>Objective</h3>
        <p>{{ plan.objective.primary }}</p>
        <p v-if="plan.objective.secondary" class="muted">{{ plan.objective.secondary }}</p>
      </section>
      <section>
        <span class="section-number">02</span><h3>Target audience</h3>
        <p>{{ plan.target_audience.primary }}</p>
        <p v-if="plan.target_audience.location" class="muted">Location: {{ plan.target_audience.location }}</p>
        <ul v-if="plan.target_audience.needs_or_motivations.length"><li v-for="item in plan.target_audience.needs_or_motivations" :key="item">{{ item }}</li></ul>
      </section>
      <section v-if="plan.offer || plan.value_proposition">
        <span class="section-number">03</span><h3>Offer & value</h3>
        <p v-if="plan.offer"><b>Offer</b>{{ plan.offer }}</p>
        <p><b>Value proposition</b>{{ plan.value_proposition }}</p>
      </section>
      <section>
        <span class="section-number">04</span><h3>Positioning</h3>
        <p>{{ plan.positioning }}</p>
        <blockquote>{{ plan.key_message }}</blockquote>
      </section>
    </div>

    <section class="plan-wide">
      <span class="section-number">05</span><h3>Strategy</h3><p>{{ plan.strategy }}</p>
    </section>
    <section v-if="plan.channels.length" class="plan-wide">
      <span class="section-number">06</span><h3>Channel strategy</h3>
      <div class="plan-cards"><article v-for="channel in plan.channels" :key="channel.name"><h4>{{ channel.name }}</h4><p>{{ channel.purpose }}</p><small>{{ channel.reason }}</small></article></div>
    </section>
    <section v-if="plan.content_direction.length" class="plan-wide">
      <span class="section-number">07</span><h3>Content direction</h3>
      <div class="plan-cards"><article v-for="content in plan.content_direction" :key="content.idea"><h4>{{ content.idea }}</h4><p>{{ content.purpose }}</p></article></div>
    </section>
    <section v-if="plan.budget_allocation" class="plan-wide budget-panel">
      <span class="section-number">08</span><h3>Budget allocation</h3>
      <strong class="budget-total">{{ money(plan.budget_allocation.total, plan.budget_allocation.currency) }}</strong>
      <div v-if="plan.budget_allocation.items.length" class="budget-list"><div v-for="item in plan.budget_allocation.items" :key="`${item.channel}-${item.amount}`"><b>{{ item.channel }}</b><span>{{ money(item.amount, plan.budget_allocation.currency) }}</span><small>{{ item.reason }}</small></div></div>
    </section>
    <section v-if="plan.timeline.length" class="plan-wide">
      <span class="section-number">09</span><h3>Timeline</h3>
      <div class="timeline"><article v-for="phase in plan.timeline" :key="`${phase.period}-${phase.phase}`"><span>{{ phase.period }}</span><h4>{{ phase.phase }}</h4><p>{{ phase.objective }}</p><ul><li v-for="activity in phase.activities" :key="activity">{{ activity }}</li></ul></article></div>
    </section>
    <section v-if="plan.kpis.length" class="plan-wide">
      <span class="section-number">10</span><h3>KPIs</h3>
      <div class="plan-cards"><article v-for="kpi in plan.kpis" :key="kpi.name"><h4>{{ kpi.name }}</h4><p>{{ kpi.purpose }}</p></article></div>
    </section>
    <div class="plan-grid plan-footer-grid">
      <section v-if="plan.assumptions_or_risks.length"><h3>Assumptions & risks</h3><ul><li v-for="risk in plan.assumptions_or_risks" :key="risk">{{ risk }}</li></ul></section>
      <section v-if="plan.next_steps.length"><h3>Recommended next steps</h3><ol><li v-for="step in plan.next_steps" :key="step">{{ step }}</li></ol></section>
    </div>
  </article>
</template>

<style scoped>
.campaign-plan{margin-top:28px;color:#243029}.plan-hero{display:flex;justify-content:space-between;gap:24px;padding:28px;border-radius:22px;background:#183e2b;color:#fff;box-shadow:0 20px 55px #193d2926}.plan-hero>div{max-width:590px}.plan-hero span,.section-number{font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.plan-hero h2{margin:7px 0 10px;font-size:30px;letter-spacing:-.035em}.plan-hero p{margin:0;color:#d6e6da;line-height:1.6}.export-plan{align-self:flex-start;white-space:nowrap;border:1px solid #ffffff4d;border-radius:11px;background:#fff;color:#183e2b;padding:10px 14px;font-weight:800;cursor:pointer}.export-plan:disabled{cursor:wait;opacity:.6}.plan-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}.plan-grid>section,.plan-wide{position:relative;padding:22px;border:1px solid #dde3dc;border-radius:17px;background:#fff}.plan-wide{margin-top:12px}.section-number{color:#6d9278}.campaign-plan h3{display:inline;margin:0 0 10px 8px;font-size:16px}.campaign-plan h4{margin:0 0 7px}.campaign-plan p{line-height:1.6}.campaign-plan ul,.campaign-plan ol{padding-left:20px;line-height:1.65}.muted,.plan-cards small{color:#727c74}.campaign-plan b{display:block;margin-bottom:3px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#738078}.campaign-plan blockquote{margin:14px 0 0;padding:13px 15px;border-left:3px solid #61a777;background:#f2f7f3;font-weight:700}.plan-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:14px}.plan-cards article{padding:15px;border-radius:13px;background:#f5f7f3}.plan-cards p{margin:0 0 5px}.budget-panel{background:#f7f3e7}.budget-total{display:block;margin:13px 0;font-size:25px;color:#183e2b}.budget-list{display:grid;gap:7px}.budget-list>div{display:grid;grid-template-columns:1fr auto;gap:3px 15px;padding:10px 0;border-top:1px solid #d9d4c5}.budget-list small{grid-column:1/-1;color:#747166}.timeline{display:grid;gap:8px;margin-top:14px}.timeline article{padding:14px 16px;border-left:3px solid #55916a;background:#f5f7f3}.timeline article>span{color:#6d9278;font-size:11px;font-weight:800;text-transform:uppercase}.timeline p{margin:5px 0}.timeline ul{margin-bottom:0}.plan-footer-grid{margin-bottom:30px}@media(max-width:700px){.plan-hero{flex-direction:column}.plan-grid{grid-template-columns:1fr}.export-plan{align-self:stretch}.plan-hero h2{font-size:25px}}
</style>
