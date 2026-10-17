<script setup lang="ts">
import { computed } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { asset } from "../api";
const props = defineProps<{ text: string; session: string; agent?: string }>();
const html = computed(() => {
  const clean = DOMPurify.sanitize(
    marked.parse(props.text, {
      gfm: true,
      breaks: false,
      async: false,
    }) as string,
    {
      FORBID_TAGS: ["style", "iframe", "object", "form"],
      FORBID_ATTR: ["style"],
    },
  );
  const dom = new DOMParser().parseFromString(clean, "text/html");
  dom.querySelectorAll("img,a").forEach((el) => {
    const attr = el.tagName === "IMG" ? "src" : "href";
    let path = el.getAttribute(attr) || "";
    if (path.startsWith("./artifacts/")) path = path.slice(2);
    if (path.startsWith("artifacts/"))
      el.setAttribute(
        attr,
        asset(
          props.session,
          props.agent && props.agent !== "main"
            ? `branches/${props.agent}/${path}`
            : path,
        ),
      );
    if (el.tagName === "A") {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
    }
    if (el.tagName === "IMG") {
      el.setAttribute("loading", "lazy");
      el.setAttribute("decoding", "async");
    }
  });
  return dom.body.innerHTML;
});
</script>
<template><div class="markdown" v-html="html" /></template>
