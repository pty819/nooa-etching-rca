// @vitest-environment jsdom
import {afterEach,describe,it,expect,vi} from 'vitest';
import {mount,flushPromises} from '@vue/test-utils';
import EvaluationPanel from '../src/components/EvaluationPanel.vue';
afterEach(()=>vi.unstubAllGlobals());
const result={review_status:'passed',prediction:{status:'scored',test_samples:60,independent_lots:12,balanced_accuracy:.85,macro_f1:.84},mechanisms:{candidate_mechanisms:{f1:1},supported_mechanisms:{f1:.667,missed_mechanisms:['contact'],false_positive_mechanisms:[]},localization_precision:.9}};
describe('host-only final evaluation',()=>{
 it('freezes explicitly and shows held-out sample/lot and mechanism scores',async()=>{
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(_url:string,options:any={})=>{calls.push(options.method||'GET');return {ok:true,json:async()=>options.method==='POST'?result:{status:'available'}};}));
  const view=mount(EvaluationPanel,{props:{session:'case',active:false}});
  try{await flushPromises();expect(view.text()).toContain('普通画图无需');await view.find('button').trigger('click');await flushPromises();expect(calls).toEqual(['GET','POST']);expect(view.text()).toContain('60 条 test 样本');expect(view.text()).toContain('12 个独立 lot');expect(view.text()).toContain('0.850');expect(view.text()).toContain('contact');}finally{view.unmount();}
 });
 it('disables freezing during an active turn and does not invent an unavailable score',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({status:'available'})})));
  const view=mount(EvaluationPanel,{props:{session:'case',active:true}});
  try{await flushPromises();expect(view.find('button').attributes('disabled')).toBeDefined();}finally{view.unmount();}
 });
 it('ignores a delayed response from an older case',async()=>{
  const pending:((value:any)=>void)[]=[];
  vi.stubGlobal('fetch',vi.fn(()=>new Promise(resolve=>pending.push(value=>resolve({ok:true,json:async()=>value})))));
  const view=mount(EvaluationPanel,{props:{session:'old',active:false}});
  try{await view.setProps({session:'new'});pending[1]({status:'unavailable'});await flushPromises();pending[0]({status:'completed',result});await flushPromises();expect(view.text()).toContain('没有提供独立');expect(view.text()).not.toContain('0.850');}finally{view.unmount();}
 });
});
