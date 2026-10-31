// @vitest-environment node
import { describe,expect,it,vi } from 'vitest';
import {workspaceReader} from './analysis-tools';
import {pmChat} from './pm';
import {imageAttachmentSchema} from '../src/city/chat-evidence';
const snapshot={revision:4,updatedAt:'2026-10-31T00:00:00Z',data:{schema:'hackathon.sources.v1',files:[{name:'stock.csv',kind:'general',hash:'hash',origin:'upload',text:'sku,stock\n'+Array.from({length:40},(_,i)=>`A${i},${i}`).join('\n')}]}};
describe('Milo evidence',()=>{
 it('reads all uploaded records through pagination, with stable revision and citations',async()=>{
  const storage={fetch:vi.fn(async(request:Request)=>request.url.endsWith('/sources')?Response.json(snapshot):Response.json({revision:0,data:{}}))};
  const read=workspaceReader(storage,'https://test');
  const catalog=JSON.parse(await read('workspace_catalog','{}'));
  expect(catalog.files[0]).toMatchObject({name:'stock.csv',records:40,columns:['sku','stock']});
  const first=JSON.parse(await read('read_source_rows',JSON.stringify({file:'stock.csv',query:'',offset:0})));
  const last=JSON.parse(await read('read_source_rows',JSON.stringify({file:'stock.csv',query:'',offset:first.nextOffset})));
  expect(first.rows).toHaveLength(30);expect(last.rows).toHaveLength(10);expect(last.rows[9]).toEqual({record:40,value:{sku:'A39',stock:'39'}});expect(last.nextOffset).toBeNull();expect(last.revision).toBe(4);
  const result=JSON.parse(await read('read_source_rows',JSON.stringify({file:'stock.csv',query:'A35',offset:0})));
  expect(result.rows[0].record).toBe(36);
  expect(JSON.parse(await read('delete_source','{}')).error).toBeDefined();
  expect(storage.fetch.mock.calls.filter(([r])=>r.url.endsWith('/sources'))).toHaveLength(1);
 });
 it('rejects remote URLs, SVG and mismatched image signatures',()=>{
  for(const dataUrl of ['https://test/image.png','data:image/svg+xml;base64,PHN2Zz4=','data:image/png;base64,YmFk'])expect(imageAttachmentSchema.safeParse({name:'image',dataUrl}).success).toBe(false);
 });
 it('passes saved image/context to Responses and completes a read-only tool loop',async()=>{
  const calls:Record<string,unknown>[]=[];
  const fetcher=vi.fn(async(url:string,init?:RequestInit)=>{
   if(url.endsWith('/models'))return Response.json({data:[{id:'gpt-4.1',created:1}]});
   calls.push(JSON.parse(String(init?.body)));
   return Response.json({output:calls.length===1?[{type:'function_call',name:'workspace_catalog',arguments:'{}',call_id:'call_1'}]:[{type:'message',content:[{type:'output_text',text:'stock.csv record 1, revision 4: 0 units.'}]}]});
  });
  vi.stubGlobal('fetch',fetcher);
  try{
   const read=vi.fn(async()=>JSON.stringify(snapshot));
   const image='data:image/png;base64,iVBORw0KGgo=';
   const response=await pmChat(new Request('https://test/chat',{method:'POST',body:JSON.stringify({model:'gpt-4.1',messages:[{role:'user',content:'Explain',analysis:{title:'Test',source:'user-assumptions',data:'{"stock":0}'},attachments:[{name:'chart.png',dataUrl:image}]}]})}),{OPENAI_API_KEY:'test'},{},read);
   expect(response.status).toBe(200);expect(read).toHaveBeenCalledWith('workspace_catalog','{}');
   expect(JSON.stringify(calls[0]?.input)).toContain('input_image');expect(JSON.stringify(calls[0]?.input)).toContain('user-assumptions');expect(JSON.stringify(calls[1]?.input)).toContain('function_call_output');
  }finally{vi.unstubAllGlobals();}
 });
});
