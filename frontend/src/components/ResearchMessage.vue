<script setup lang="ts">
import { ref, computed } from "vue";
import { FlaskConical, LoaderCircle, ChevronDown } from "lucide-vue-next";
import MarkdownView from "./MarkdownView.vue";
import type { Msg } from "../types";
const props = defineProps<{ msg: Msg; session: string }>();
const expanded = ref(false);
const long = computed(
  () => props.msg.legacy && props.msg.markdown.length > 1700,
);
</script>
<template>
  <article
    :class="['message', msg.role, { provisional: msg.provisional }]"
    :data-message-id="msg.id"
  >
    <div class="message-label">
      <span v-if="msg.role === 'user'" class="avatar user-avatar">你</span
      ><span v-else class="avatar agent-avatar"
        ><FlaskConical :size="15" /></span
      ><strong>{{ msg.role === "user" ? "设备专家" : "NOOA" }}</strong
      ><span>{{
        msg.legacy
          ? "历史记录"
          : msg.provisional
            ? "正在回复"
            : msg.publication === "final"
              ? "最终交接"
              : msg.publication === "progress"
                ? "研究进度 · 待审查"
                : "研究消息"
      }}</span
      ><LoaderCircle v-if="msg.provisional" :size="12" class="spin" />
    </div>
    <div :class="['message-body', { clamped: long && !expanded }]">
      <MarkdownView
        :text="msg.markdown"
        :session="session"
        :agent="msg.agent_id"
      />
    </div>
    <button
      v-if="long"
      class="text-button expand"
      @click="expanded = !expanded"
    >
      {{ expanded ? "收起历史内容" : "展开完整内容" }}<ChevronDown :size="13" />
    </button>
  </article>
</template>
