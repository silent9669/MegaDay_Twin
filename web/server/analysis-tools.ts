import { z } from 'zod';
import { inspect, type SourcePack } from '../src/studio/sources';
import type { Doc } from '../src/studio/workspace-types';
type Storage = { fetch(request: Request): Promise<Response> };
const argsSchema = z.object({file:z.string().max(250),query:z.string().max(150),offset:z.number().int().min(0).max(1000000)});
export const analysisTools = [
  {type:'function',name:'workspace_catalog',description:'List all uploaded source files, columns and row counts, plus saved HQ product notes/assumptions and sandbox state. Read-only snapshot; not external live accounts.',strict:true,parameters:{type:'object',properties:{},required:[],additionalProperties:false}},
  {type:'function',name:'read_source_rows',description:'Read up to 30 records from an uploaded source by exact catalog filename. query is an optional case-insensitive text search across row values; use empty string for all rows. offset paginates matching rows. Returns original 1-based record numbers for citation. Nested JSON remains data; no inferred joins.',strict:true,parameters:{type:'object',properties:{file:{type:'string'},query:{type:'string'},offset:{type:'integer'}},required:['file','query','offset'],additionalProperties:false}},
];
export function workspaceReader(storage:Storage,url:string) {
  let snapshot:Promise<Doc<SourcePack>>|undefined;
  const parsed=new Map<string,ReturnType<typeof inspect>>();
  const load=()=>snapshot??=(async()=>{const response=await storage.fetch(new Request(new URL('/api/workspace/sources',url)));if(!response.ok)throw Error('Workspace sources unavailable.');return response.json() as Promise<Doc<SourcePack>>;})();
  return async (name:string,args:string):Promise<string>=>{
    try {
      const document=await load();
      const parse=(file:SourcePack['files'][number])=>{let result=parsed.get(file.name);if(!result){result=inspect(file.name,file.text);parsed.set(file.name,result);}return result;};
      if(name==='workspace_catalog') {
        const documents:Record<string,unknown>={};
        for(const key of ['hq','sandbox']) {const res=await storage.fetch(new Request(new URL(`/api/workspace/${key}`,url)));if(res.ok){const doc=await res.json() as Doc<unknown>;const text=JSON.stringify(doc);documents[key]=text.length>16000?{revision:doc.revision,preview:text.slice(0,16000),truncated:true}:doc;}}
        return JSON.stringify({revision:document.revision,updatedAt:document.updatedAt,files:document.data.files.map(file=>({name:file.name,kind:file.kind,hash:file.hash,columns:parse(file).columns,records:parse(file).rows.length})),documents});
      }
      if(name!=='read_source_rows')return JSON.stringify({error:'Unknown read-only tool.'});
      const {file,query,offset}=argsSchema.parse(JSON.parse(args));
      const source=document.data.files.find(f=>f.name===file);
      if(!source)return JSON.stringify({error:'File not found. Use workspace_catalog for exact names.'});
      const all=parse(source).rows.map((value,index)=>({record:index+1,value}));
      const matches=query?all.filter(row=>JSON.stringify(row.value).toLowerCase().includes(query.toLowerCase())):all;
      const rows:unknown[]=[];let size=0;
      for(const row of matches.slice(offset,offset+30)) {
        const text=JSON.stringify(row);
        if(rows.length&&size+text.length>20000)break;
        rows.push(text.length>20000?{record:row.record,preview:text.slice(0,20000),truncated:true}:row);size+=Math.min(text.length,20000);
      }
      return JSON.stringify({file,revision:document.revision,hash:source.hash,totalRecords:all.length,matchingRecords:matches.length,offset,rows,nextOffset:offset+rows.length<matches.length?offset+rows.length:null});
    }catch{return JSON.stringify({error:'Could not read these records. Check the file name and parameters.'});}
  };
}
