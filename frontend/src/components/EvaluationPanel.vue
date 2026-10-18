<script setup lang="ts">
import {ref,watch} from 'vue';
import {api} from '../api';
const props=defineProps<{session:string;active:boolean}>();
const state=ref<any>(null),error=ref(''),busy=ref(false);
let generation=0;
async function load(){
 const own=++generation;
 try{const result=await api(`/sessions/${props.session}/evaluation`);if(own===generation)state.value=result;}
 catch(e:any){if(own===generation)error.value=e.message;}
}
watch(()=>[props.session,props.active],()=>{error.value='';busy.value=false;void load();},{immediate:true});
async function finalize(){
 const own=++generation;busy.value=true;error.value='';
 try{const result=await api(`/sessions/${props.session}/evaluation`,'POST');if(own===generation)state.value={status:'completed',result};}
 catch(e:any){if(own===generation)error.value=e.message;}
 finally{if(own===generation)busy.value=false;}
}
function score(value:any){return typeof value==='number'?value.toFixed(3):'未评分';}
</script>
<template>
 <section class="dataset-panel evaluation-panel">
  <h3>主研究收尾评估</h3>
  <p class="files-note">研究与多轮指导完成后，冻结当前报告和已选方法，再使用单独保存的 test 与仿真参考评分。评分不会作为研究工具返回给 agent。</p>
  <p v-if="error" role="alert" class="formula-failure">{{error}}</p>
  <p v-if="state?.status==='unavailable'" class="files-note">这份会话没有提供独立 test／仿真参考，不生成替代分数。已有研究和图表仍可继续使用。</p>
  <template v-else-if="state?.status==='available'">
   <p>冻结后只能重试同一方法，不能根据本次 test 分数改写后再次选优。普通画图无需启动此步骤。</p>
   <button :disabled="active || busy" @click="finalize">{{busy?'正在独立计算…':'冻结并评估当前报告'}}</button>
  </template>
  <template v-else-if="state?.status==='completed'">
   <p class="formula-status">本次定义已冻结 · {{state.result.review_status || '审查状态未记录'}}</p>
   <h4>预测表现</h4>
   <template v-if="state.result.prediction.status==='scored'">
    <p>{{state.result.prediction.test_samples}} 条 test 样本 · {{state.result.prediction.independent_lots}} 个独立 lot</p>
    <table><tbody><tr><th>Balanced accuracy</th><td>{{score(state.result.prediction.balanced_accuracy)}}</td></tr><tr><th>Macro F1</th><td>{{score(state.result.prediction.macro_f1)}}</td></tr></tbody></table>
   </template>
   <p v-else class="files-note">没有冻结的正式模型选择，本次只评机制报告。</p>
   <h4>仿真参考下的机制定位</h4>
   <table><tbody><tr><th>候选机制 F1</th><td>{{score(state.result.mechanisms.candidate_mechanisms.f1)}}</td></tr><tr><th>已支持机制 F1</th><td>{{score(state.result.mechanisms.supported_mechanisms.f1)}}</td></tr><tr><th>传感器／step 定位精度</th><td>{{score(state.result.mechanisms.localization_precision)}}</td></tr></tbody></table>
   <p>漏掉的机制：{{state.result.mechanisms.supported_mechanisms.missed_mechanisms.join('、') || '无'}}</p>
   <p>误报机制：{{state.result.mechanisms.supported_mechanisms.false_positive_mechanisms.join('、') || '无'}}</p>
   <p class="files-note">数值对应本次冻结定义。仿真参考评分用于检查研究方法，实际设备机制仍需专家核实。</p>
  </template>
  <p v-else class="files-note">正在读取评估状态…</p>
 </section>
</template>
