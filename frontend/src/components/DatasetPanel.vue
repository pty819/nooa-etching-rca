<script setup lang="ts">
import { ref, watch } from "vue";
import type { DatasetState } from "../types";
import { api } from "../api";
const props = defineProps<{
  session: string;
  agent: string;
  active: boolean;
  supplied: string[];
  revision?: number;
}>();
const emit = defineEmits<{ changed: [] }>();
const state = ref<DatasetState | null>(null),
  error = ref(""),
  busy = ref(false),
  source = ref(""),
  reason = ref(""),
  preview = ref<any>(null);
let generation = 0;
const names: Record<string, string> = {
  table: "表格",
  timeseries: "时间序列",
  image: "图片",
  text: "文本",
  mixed: "组合资产",
};
async function load() {
  const own = ++generation;
  try {
    const value = await api(
      `/sessions/${props.session}/datasets?agent_id=${encodeURIComponent(props.agent)}`,
    );
    if (own === generation) state.value = value;
  } catch (e: any) {
    if (own === generation) error.value = e.message;
  }
}
watch(
  () => [props.session, props.agent, props.revision],
  () => {
    busy.value = false;
    preview.value = null;
    state.value = null;
    error.value = "";
    void load();
  },
  { immediate: true },
);
async function select(id = "", back = "") {
  busy.value = true;
  error.value = "";
  const own = ++generation;
  try {
    const result = await api(
      `/sessions/${props.session}/datasets/select`,
      "POST",
      { dataset_ref: id, back, agent_id: props.agent },
    );
    if (own === generation) {
      state.value = result;
      preview.value = null;
      emit("changed");
    }
  } catch (e: any) {
    if (own === generation) error.value = e.message;
  } finally {
    if (own === generation) busy.value = false;
  }
}
async function inspect(id: string) {
  const session = props.session,
    agent = props.agent;
  try {
    const data = await api(`/sessions/${session}/datasets/${id}`);
    if (props.session === session && props.agent === agent)
      preview.value = data;
  } catch (e: any) {
    error.value = e.message;
  }
}
async function importLocal() {
  busy.value = true;
  error.value = "";
  const own = ++generation;
  try {
    const result = await api(
      `/sessions/${props.session}/datasets/import`,
      "POST",
      { dataset: source.value, reason: reason.value, agent_id: props.agent },
    );
    if (own === generation) {
      state.value = result.state;
      emit("changed");
      reason.value = "";
    }
  } catch (e: any) {
    if (own === generation) error.value = e.message;
  } finally {
    if (own === generation) busy.value = false;
  }
}
async function loadMore() {
  if (!state.value) return;
  const own = generation;
  try {
    const result = await api(
      `/sessions/${props.session}/datasets?agent_id=${encodeURIComponent(props.agent)}&offset=${state.value.next_offset || state.value.datasets.length}`,
    );
    if (own === generation && state.value) {
      const known = new Set(state.value.datasets.map((d) => d.id));
      state.value.datasets.push(
        ...result.datasets.filter((d: any) => !known.has(d.id)),
      );
      state.value.next_offset = result.next_offset;
    }
  } catch (e: any) {
    if (own === generation) error.value = e.message;
  }
}
defineExpose({ load });
</script>
<template>
  <section class="dataset-panel">
    <p class="files-note">
      选择本轮研究的数据范围。子集保留父版本、划分代码与理由；返回主集不会删除子集。
    </p>
    <p v-if="error" class="formula-failure" role="alert">{{ error }}</p>
    <template v-if="state">
      <div class="dataset-current">
        <small
          >当前数据 ·
          {{ names[state.current.modality] || state.current.modality }}</small
        >
        <h3>{{ state.current.title }}</h3>
        <p>
          {{ state.current.rows.toLocaleString() }} 个样本 · 版本
          {{ state.current.version }}
        </p>
        <code>{{ state.current.id }}</code>
        <div class="dataset-actions">
          <button
            :disabled="active || busy || !state.scope.stack.length"
            @click="select('', 'parent')"
          >
            返回上一范围
          </button>
          <button
            :disabled="
              active || busy || state.scope.current === state.scope.root
            "
            @click="select('', 'root')"
          >
            返回主集
          </button>
        </div>
      </div>
      <p v-if="active" class="files-note">
        研究运行中，请在对话里指导 agent 切换范围。
      </p>
      <h4>数据血缘</h4>
      <div class="dataset-lineage" aria-label="数据血缘">
        <div
          v-for="node in state.lineage.nodes"
          :key="node.id"
          class="dataset-node"
        >
          <strong>{{ node.title }}</strong
          ><small>{{ node.rows }} 样本 · {{ names[node.modality] }}</small>
          <code>{{ node.id }}</code>
          <p v-if="node.reason">{{ node.reason }}</p>
          <small v-for="parent in node.parents" :key="parent"
            >←
            {{
              state.lineage.nodes.find((n) => n.id === parent)?.title || parent
            }}</small
          >
        </div>
      </div>
      <h4>已登记版本 · {{ state.total }}</h4>
      <div
        v-for="dataset in state.datasets"
        :key="dataset.id"
        class="dataset-entry"
      >
        <div>
          <strong>{{ dataset.title }}</strong
          ><small
            >{{ dataset.rows }} 样本 · {{ names[dataset.modality] }} · v{{
              dataset.version
            }}</small
          >
        </div>
        <button @click="inspect(dataset.id)">详情</button>
        <button
          :disabled="active || busy || state.scope.current === dataset.id"
          @click="select(dataset.id)"
        >
          {{ state.scope.current === dataset.id ? "当前" : "使用" }}
        </button>
      </div>
      <button
        v-if="
          state.next_offset !== undefined && state.next_offset < state.total
        "
        @click="loadMore"
      >
        读取更多版本
      </button>
      <details v-if="preview" open class="dataset-preview">
        <summary>{{ preview.title }} · schema 与预览</summary>
        <p>{{ preview.reason }}</p>
        <pre>{{ JSON.stringify(preview.schema, null, 2) }}</pre>
        <pre>{{ JSON.stringify(preview.preview, null, 2) }}</pre>
        <pre v-if="preview.transform_source">{{
          preview.transform_source
        }}</pre>
        <p>
          划分源码摘要：<code>{{
            preview.transform_sha256 || "原始导入"
          }}</code>
        </p>
      </details>
      <details class="dataset-import">
        <summary>登记已提供的本地数据</summary>
        <p class="files-note">
          从项目 data
          目录中已提供的文件或目录导入为新版本。外部拉数与知识图谱暂未接入。
        </p>
        <label
          >数据来源<select v-model="source">
            <option disabled value="">选择本地输入</option>
            <option v-for="item in supplied" :key="item">{{ item }}</option>
          </select></label
        >
        <label
          >加入理由<textarea
            v-model="reason"
            placeholder="说明这份数据与当前研究的关系"
          />
        </label>
        <button
          :disabled="active || busy || !source || !reason.trim()"
          @click="importLocal"
        >
          登记新数据
        </button>
      </details>
    </template>
    <p v-else-if="!error" class="files-note">正在读取数据范围…</p>
  </section>
</template>
