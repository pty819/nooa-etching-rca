// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import FormulaStudies from '../src/components/FormulaStudies.vue';
import type { FormulaStudy } from '../src/types';
const sample = (): FormulaStudy => ({
  id:'study-123',owner:'main',revision:1,selection_id:null,limits:'开发集参与选择，不是独立测试。',
  plan:{title:'专家融合公式',labels:['good','bad'],metric:'balanced_accuracy',folds:3,input_sha256:'abc',train:['a','b'],dev:['c','d'],model_params:{logistic:{C:1}}},
  formulas:[{ref:'product@1',expression:'mean(a*b)',description:'step2后80%',sensors:['a','b'],steps:[2],unit:'V2',params:{start_fraction:.2},sha256:'source-hash'}],
  sets:[{id:'set-1',formulas:['product@1'],excluded:null}],
  trials:[{id:'trial-1',set_id:'set-1',formulas:['product@1'],model:'logistic',status:'succeeded',dev_score:1,cv_mean:1,cv_std:0}],selections:[],
});
describe('formula comparison', () => {
  it('distinguishes pending comparison from replayed selection and preserves versions', async () => {
    const study=sample();
    const view=mount(FormulaStudies,{props:{studies:[study],session:'case-a'}});
    expect(view.text()).toContain('尚未确认最佳公式');
    expect(view.text()).toContain('mean(a*b)');
    expect(view.text()).toContain('1.0000');
    expect(view.text()).toContain('step 2');
    study.selection_id='selection-1';
    study.selections.push({id:'selection-1',winner:'trial-1',ranking:['trial-1'],replay:{status:'passed'}});
    await view.setProps({studies:[structuredClone(study)]});
    expect(view.text()).toContain('最佳候选已重跑核对');
    expect(view.find('a').attributes('href')).toContain('/api/sessions/case-a/artifacts/artifacts/formula-lab/study-123/study.json');
    view.unmount();
  });
  it('keeps scientific failures and explicit exclusion reasons visible', () => {
    const study=sample();
    study.trials.push({id:'trial-2',set_id:'set-2',formulas:['product@2'],model:'random_forest',status:'failed',error:'formula replay changed'});
    study.sets.push({id:'set-2',formulas:['product@2'],excluded:{reason:'量纲不适用'}});
    const view=mount(FormulaStudies,{props:{studies:[study],session:'case-a'}});
    expect(view.text()).toContain('formula replay changed');
    expect(view.text()).toContain('量纲不适用');
    expect(view.text()).toContain('尚未确认最佳公式');
    view.unmount();
  });
});
