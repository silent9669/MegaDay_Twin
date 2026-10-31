import { describe,expect,it } from 'vitest';
import { blankWorksheet,exampleWorksheet,worksheetResults,hqSchema,defaultHQ } from './types';
describe('product comparison worksheet',()=>{
 it('reproduces the reference calculation without altering model simulations',()=>{
  expect(worksheetResults(exampleWorksheet)).toEqual([
   {units:83,leftover:0,revenue:49800000,costs:46065000,beforeAds:3735000,spend:1200000,afterAds:2535000},
   {units:63,leftover:20,revenue:37800000,costs:34965000,beforeAds:2835000,spend:0,afterAds:2835000},
  ]);
 });
 it('requires explicit inputs, caps sales at stock, and supports zero',()=>{
  expect(worksheetResults(blankWorksheet)).toBeNull();
  expect(worksheetResults({...exampleWorksheet,stock:0})?.[0]).toMatchObject({units:0,revenue:0,afterAds:-1200000});
  expect(worksheetResults({...exampleWorksheet,continueUnits:1000})?.[0]?.units).toBe(83);
 });
 it('rejects invalid saved assumptions and duplicate products',()=>{
  expect(hqSchema.safeParse({...defaultHQ,worksheets:{a:{...exampleWorksheet,feePercent:101}}}).success).toBe(false);
  const product={sku:'A',name:'A',mode:'workspace'};
  expect(hqSchema.safeParse({...defaultHQ,tracked:[product,product]}).success).toBe(false);
 });
});
