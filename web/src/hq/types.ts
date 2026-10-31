import { z } from 'zod';
export const worksheetSchema = z.object({
  stock: z.number().min(0).max(1e9).nullable(),
  price: z.number().min(0).max(1e12).nullable(),
  cost: z.number().min(0).max(1e12).nullable(),
  packaging: z.number().min(0).max(1e12).nullable(),
  feePercent: z.number().min(0).max(100).nullable(),
  continueUnits: z.number().min(0).max(1e9).nullable(),
  pauseUnits: z.number().min(0).max(1e9).nullable(),
  spend: z.number().min(0).max(1e12).nullable(),
});
export type Worksheet = z.infer<typeof worksheetSchema>;
export const blankWorksheet: Worksheet = {stock:null,price:null,cost:null,packaging:null,feePercent:null,continueUnits:null,pauseUnits:null,spend:null};
export const exampleWorksheet: Worksheet = {stock:83,price:600000,cost:540000,packaging:3000,feePercent:2,continueUnits:83,pauseUnits:63,spend:1200000};
export const trackedProductSchema = z.object({sku:z.string().min(1).max(150),name:z.string().max(250),mode:z.enum(['workspace','model'])});
export type TrackedProduct = z.infer<typeof trackedProductSchema>;
export const productKey = (product: Pick<TrackedProduct,'mode'|'sku'>) => `${product.mode}:${product.sku}`;
export const hqSchema = z.object({
  tracked:z.array(trackedProductSchema).max(30),
  active:z.string().max(160).nullable(),
  worksheets:z.record(z.string().max(160),worksheetSchema).refine(x=>Object.keys(x).length<=30),
  activity:z.array(z.object({id:z.string().uuid(),product:z.string().max(160),at:z.string().datetime(),action:z.string().max(120),detail:z.string().max(2000)})).max(200),
}).superRefine((data,ctx)=>{if(new Set(data.tracked.map(productKey)).size!==data.tracked.length)ctx.addIssue({code:'custom',message:'Duplicate tracked product.'});});
export type HQDocument = z.infer<typeof hqSchema>;
export const defaultHQ: HQDocument = {tracked:[],active:null,worksheets:{},activity:[]};
export function worksheetResults(w: Worksheet) {
  if(Object.values(w).some(value=>value===null)) return null;
  const stock=w.stock!,price=w.price!,unitCost=w.cost!+w.packaging!+price*w.feePercent!/100;
  return [w.continueUnits!,w.pauseUnits!].map((demand,index)=>{
    const units=Math.min(stock,demand),revenue=units*price,costs=units*unitCost,spend=index===0?w.spend!:0;
    return {units,leftover:stock-units,revenue,costs,beforeAds:revenue-costs,spend,afterAds:revenue-costs-spend};
  });
}
