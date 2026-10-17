// @vitest-environment jsdom
import { describe,it,expect,vi,afterEach } from 'vitest';
import { defineComponent,h } from 'vue';
import { mount,flushPromises } from '@vue/test-utils';
import DatasetPanel from '../src/components/DatasetPanel.vue';
import { useResearch } from '../src/useResearch';
const root={id:'ds-root',title:'主数据',rows:32,parents:[],version:1,family:'ds-root',relation:'import',modality:'table',reason:'初始输入'};
const child={...root,id:'ds-child',title:'每批第一片',rows:16,parents:[root.id],reason:'专家指定的分组子集'};
const state=(selected=child)=>({datasets:[child,root],total:2,current:selected,scope:{root:root.id,current:selected.id,stack:selected.id===root.id?[]:[root.id]},lineage:{nodes:[child,root],edges:[{parent:root.id,child:child.id}]},acquisition:{status:'unavailable',message:'not configured'}});
afterEach(()=>vi.unstubAllGlobals());
describe('dataset versions',()=>{
 it('shows lineage and returns to the parent without deleting child data',async()=>{
  const calls:any[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,options:any={})=>{if(options.body)calls.push(JSON.parse(options.body));return {ok:true,json:async()=>options.method==='POST'?state(root):state()};}));
  const view=mount(DatasetPanel,{props:{session:'case',agent:'main',active:false,supplied:['table.csv']}});
  try {
   await flushPromises();expect(view.text()).toContain('16 个样本');expect(view.text()).toContain('专家指定的分组子集');
   await view.findAll('button').find(b=>b.text()==='返回上一范围')!.trigger('click');await flushPromises();
   expect(calls[0].back).toBe('parent');expect(view.text()).toContain('32 个样本');expect(view.text()).toContain('每批第一片');
   expect(view.text()).toContain('知识图谱暂未接入');
  }finally{view.unmount();}
 });
 it('prevents UI scope changes during active research',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>state()})));
  const view=mount(DatasetPanel,{props:{session:'case',agent:'main',active:true,supplied:[]}});
  try{await flushPromises();expect(view.findAll('button').find(b=>b.text()==='返回主集')!.attributes('disabled')).toBeDefined();expect(view.text()).toContain('对话里指导');}finally{view.unmount();}
 });
 it('discards stale metadata responses within the same research session',async()=>{
  let delayed=false;const pending:((data:any)=>void)[]=[];
  class Source{onopen=null;onerror=null;addEventListener(){}close(){}}
  const meta={id:'case',title:'case',active:false,targets:32,controls:0,updated:1};
  const detail=(revision:number)=>({...meta,history:[],artifacts:[],branches:[],last_event_id:0,formula_studies:[{id:'study',revision,selection_id:revision===1?'stale':null}]});
  vi.stubGlobal('EventSource',Source);
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
   if(url==='/api/config')return Promise.resolve({ok:true,json:async()=>({providers:[],datasets:[]})});
   if(url==='/api/sessions')return Promise.resolve({ok:true,json:async()=>[meta]});
   if(delayed)return new Promise(resolve=>pending.push(data=>resolve({ok:true,json:async()=>data})));
   return Promise.resolve({ok:true,json:async()=>detail(0)});
  }));
  const view=mount(defineComponent({setup(_,{expose}){const r=useResearch();expose(r);return()=>h('div');}}));
  try{await flushPromises();const r=view.vm as any;delayed=true;const old=r.loadArtifacts(),newer=r.loadArtifacts();pending[1](detail(2));await newer;pending[0](detail(1));await old;expect(r.s.formulaStudies[0].revision).toBe(2);expect(r.s.formulaStudies[0].selection_id).toBeNull();}finally{view.unmount();}
 });
});
