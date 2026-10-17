<script setup lang="ts">
import type { FormulaStudy } from '../types';
import { asset } from '../api';
defineProps<{ studies: FormulaStudy[]; session: string }>();
const labels: Record<string, string> = {running:'比较中', succeeded:'已完成', failed:'失败', interrupted:'已中断'};
const winner = (study: FormulaStudy) => study.trials.find(t => t.id === study.selections.find(s => s.id === study.selection_id)?.winner);
const score = (value?: number) => value === undefined ? '—' : value.toFixed(4);
const ordered = (study: FormulaStudy) => {
  const selected = study.selections.find(s => s.id === study.selection_id);
  if (selected) return selected.ranking.map(id => study.trials.find(t => t.id === id)!).filter(Boolean);
  return study.trials;
};
const path = (study: FormulaStudy, name: string) => `artifacts/formula-lab/${study.id}/${name}`;
</script>

<template>
  <div class="formula-studies">
    <p class="files-note">专家的公式、原始数据计算、模型比较与重跑结果在这里保留，可跨轮继续。</p>
    <div v-if="!studies.length" class="empty-panel">
      <p>还没有公式比较任务。</p>
      <small>可以在对话中提出：从 step 2 的 20% 开始，比较 mean(a × b) 与振动强度，用 Logistic 和树模型评估。</small>
    </div>
    <section v-for="study in studies" :key="study.id" class="formula-study">
      <h3>{{ study.plan.title }}</h3>
      <p class="formula-state" :class="{ verified: !!study.selection_id }">{{ study.selection_id ? '最佳候选已重跑核对' : '尚未确认最佳公式 · 待完成比较或重跑' }}</p>
      <p v-if="winner(study)" class="formula-champion">最佳候选：{{ winner(study)?.formulas.join(' + ') }} · {{ winner(study)?.model }}<br />开发集 {{ score(winner(study)?.dev_score) }} · lot CV {{ score(winner(study)?.cv_mean) }}</p>
      <small>{{ study.plan.labels.join(' ↔ ') }} · {{ study.plan.metric }} · 按 lot 分组 {{ study.plan.folds }} 折<br />训练 {{ study.plan.train.length }} 片 / 开发集 {{ study.plan.dev.length }} 片 · 研究者 {{ study.owner }}</small>
      <details class="formula-protocol">
        <summary>固定验证协议与参数</summary>
        <p>数据摘要 {{ study.plan.input_sha256 }}</p>
        <pre>{{ JSON.stringify(study.plan.model_params, null, 2) }}</pre>
        <p>开发集主指标降序，再比较 CV 均值、CV 波动和公式数量。预处理在各训练折内拟合。</p>
      </details>
      <details v-for="formula in study.formulas" :key="formula.ref" class="formula-version">
        <summary>{{ formula.ref }} · {{ formula.expression }}</summary>
        <p>{{ formula.description }}</p>
        <p v-if="formula.change_reason">修订原因：{{ formula.change_reason }}</p>
        <p>step {{ formula.steps.join(', ') }} · {{ formula.sensors.join(' × ') }} · {{ formula.unit }}</p>
        <pre>{{ JSON.stringify(formula.params, null, 2) }}</pre>
        <small>源码 SHA256 {{ formula.sha256 }}</small>
      </details>
      <div v-if="study.trials.length" class="formula-table-scroll">
        <table class="formula-table">
          <thead><tr><th>公式 / 模型</th><th>开发集</th><th>lot CV</th><th>状态</th></tr></thead>
          <tbody><tr v-for="trial in ordered(study)" :key="trial.id">
            <td>{{ trial.formulas.join(' + ') }}<small>{{ trial.model }}</small></td>
            <td>{{ score(trial.dev_score) }}</td>
            <td>{{ score(trial.cv_mean) }}<small>± {{ score(trial.cv_std) }}</small></td>
            <td>{{ labels[trial.status] || trial.status }}</td>
          </tr></tbody>
        </table>
      </div>
      <details v-for="trial in study.trials.filter(t => t.error)" :key="trial.id" class="formula-failure">
        <summary>{{ trial.formulas.join(' + ') }} / {{ trial.model }} · {{ labels[trial.status] }}</summary>
        <pre>{{ trial.error }}</pre>
      </details>
      <p v-for="attempt in (study.replays || []).filter(r => r.status !== 'passed')" :key="attempt.id" class="formula-failure">重跑未完成：{{ attempt.error || attempt.status }}</p>
      <p v-for="set in study.sets.filter(s => s.excluded)" :key="set.id" class="formula-excluded">已排除 {{ set.formulas.join(' + ') }}：{{ set.excluded?.reason }}</p>
      <p class="files-note">{{ study.limits }}</p>
      <a :href="asset(session, path(study, 'study.json'))" target="_blank" rel="noreferrer">下载研究记录</a>
      <span v-if="study.trials.some(t => t.status === 'succeeded')"> · <a :href="asset(session, path(study, 'ranking.csv'))" target="_blank" rel="noreferrer">下载比较表</a></span>
    </section>
  </div>
</template>
