import {chromium,webkit} from 'playwright';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const base=process.env.QA_BASE_URL||'http://127.0.0.1:5179';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const out=new URL('../../qa/runs/hq-workspace/',import.meta.url);await mkdir(out,{recursive:true});
const files=[
 {name:'hq-stock.csv',kind:'inventory',text:'Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nQA-A,Travel mug,83,2026-10-31 09:00\nQA-B,Coffee cup,20,2026-10-31 09:00'},
 {name:'hq-campaigns.json',kind:'campaigns',text:JSON.stringify([{ma_chien_dich:'QA-C1',ten_chien_dich:'Morning essentials',sku:['QA-A','QA-B'],ngan_sach_ngay:1200000,bat_dau:'2026-10-31 09:00'}])},
 {name:'hq-ads.csv',kind:'ads',text:'Mã chiến dịch,Chi phí,Lượt click,Thời gian,Lượt hiển thị\nQA-C1,120000,32,2026-10-31 09:00+07:00,1000'},
];
for(const [engineName,engine] of Object.entries({chromium,webkit})){
 const browser=await engine.launch();try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Origin:base},reducedMotion:'reduce'});page.setDefaultTimeout(15000);const errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const login=await page.request.post(base+'/api/auth/login',{data:{username:'admin',password:(await readFile('/tmp/megaday-qa-password','utf8')).trim()}});assert(login.ok(),'Local login failed');
  for(const [key,data] of Object.entries({sources:{schema:'hackathon.sources.v1',files:files.map(f=>({...f,hash:'qa',origin:'QA upload'}))},hq:{tracked:[],active:null,worksheets:{},activity:[]},chats:{preferredModel:'gpt-4.1',sessions:[]}})){
   const old=await (await page.request.get(base+'/api/workspace/'+key)).json();const result=await page.request.put(base+'/api/workspace/'+key,{data:{revision:old.revision,data}});assert(result.ok(),`${key}: ${await result.text()}`);
  }
  await page.addInitScript(()=>localStorage.setItem('megaday.city.tour.v4','complete'));
  await page.route('**/api/pm/models',route=>route.fulfill({json:{configured:true,models:['gpt-4.1'],defaultModel:'gpt-4.1'}}));
  let requests=0;
  await page.route('**/api/pm/chat',async route=>{requests++;const body=route.request().postDataJSON();const doc=await(await page.request.get(base+'/api/workspace/chats')).json();const session=doc.data.sessions.find(s=>s.id===body.sessionId);assert(session.messages.at(-1).analysis);assert(session.messages.at(-1).attachments?.[0]?.dataUrl.startsWith('data:image/jpeg;base64,'));session.messages.push({id:crypto.randomUUID(),role:'assistant',content:'QA mocked reply: comparison uses your assumptions.'});await page.request.put(base+'/api/workspace/chats',{data:{revision:doc.revision,data:doc.data}});await route.fulfill({json:{reply:'QA mocked reply: comparison uses your assumptions.'}});});
  console.log(engineName+': setup');await page.goto(base+'/city-demo');await page.getByRole('button',{name:'MegaDay HQ',exact:true}).click();const hq=page.getByRole('dialog',{name:'MegaDay HQ',exact:true});await hq.waitFor();
  assert((await hq.boundingBox()).width>=1000);assert.equal(await page.getByRole('button',{name:'Hide workspace',exact:true}).count(),0);
  await page.getByText('Travel mug',{exact:true}).waitFor();await page.getByRole('row').filter({hasText:'Travel mug'}).getByRole('button',{name:'Compare',exact:true}).click();
  await page.getByRole('button',{name:'Load reference example'}).click();await page.getByText('2,535,000 VND',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'SKU Details',exact:true}).click();await page.getByRole('tab',{name:'Comparisons',exact:true}).click();assert.equal(await page.getByLabel('Available units',{exact:true}).inputValue(),'83');
  await page.getByRole('button',{name:'Save assumptions'}).click();await page.getByRole('button',{name:'Analyze with AI',exact:true}).click();const chat=page.getByRole('dialog',{name:'Milo',exact:true});await chat.waitFor();assert.equal(requests,0);
  const a=await hq.boundingBox(),b=await chat.boundingBox();assert(b.x+b.width<=a.x+1,'Panels overlap');
  await hq.locator('.district-workspace-body').evaluate(element=>{element.scrollTop=0;});await page.screenshot({path:new URL(`${engineName}-comparison-chat.png`,out).pathname});
  // Editing in HQ must not close the nonmodal chat.
  await page.getByLabel('New ad spend (VND)',{exact:true}).fill('1000000');assert(await chat.isVisible());
  await page.locator('input[type=file]').setInputFiles(new URL('../public/assets/milo/avatar.png',import.meta.url).pathname);await page.getByRole('button',{name:'Remove avatar.png'}).waitFor();console.log(engineName+': simultaneous panels checked');await page.getByRole('button',{name:'Send message'}).click();await page.getByText('QA mocked reply: comparison uses your assumptions.').waitFor();assert.equal(requests,1);
  console.log(engineName+': reply checked');await page.getByRole('tab',{name:'Campaigns',exact:true}).click();await page.getByRole('heading',{name:'Morning essentials',exact:true}).waitFor();
  await page.getByRole('tab',{name:'Activity Logs',exact:true}).click();await page.getByLabel('Add a note').fill('Check the ad cost assumptions');await page.getByRole('button',{name:'Save note'}).click();await page.getByText('Check the ad cost assumptions',{exact:true}).waitFor();
  console.log(engineName+': notes checked');await page.getByRole('button',{name:'Close chat'}).click();await page.getByRole('button',{name:'Close workspace'}).click();await page.getByRole('heading',{name:'Commerce city',exact:true}).waitFor();assert.equal(await hq.count(),0);
  await page.getByRole('button',{name:'Warehouse',exact:true}).click();assert.equal(await page.getByRole('link',{name:'Manage sources',exact:true}).count(),0);await page.getByRole('button',{name:'Close workspace'}).click();
  await page.getByRole('button',{name:'Ads Platform',exact:true}).click();await page.getByRole('heading',{name:'Ad observations'}).waitFor();await page.getByRole('link',{name:'Campaigns in HQ'}).click();await hq.waitFor();await page.getByRole('heading',{name:'Morning essentials',exact:true}).waitFor();
  // Small desktop: both panels still fit; mobile: one panel at a time.
  await page.setViewportSize({width:1024,height:800});await page.getByRole('button',{name:'Open PM assistant',exact:true}).first().click();await chat.waitFor();const smallHQ=await hq.boundingBox(),smallChat=await chat.boundingBox();assert(smallChat.x+smallChat.width<=smallHQ.x+1);await page.screenshot({path:new URL(`${engineName}-1024.png`,out).pathname});
  await page.setViewportSize({width:390,height:844});await chat.waitFor();assert.equal(await hq.count(),0);await page.getByRole('button',{name:'Close chat'}).click();await hq.waitFor();assert((await hq.boundingBox()).width<=390);await page.screenshot({path:new URL(`${engineName}-mobile.png`,out).pathname});
  await page.getByRole('button',{name:'Close workspace'}).click();await page.getByRole('heading',{name:'Commerce city',exact:true}).waitFor();
  await page.getByRole('button',{name:'Replay tutorial',exact:true}).click();await page.getByRole('heading',{name:'Start at HQ',exact:true}).waitFor();await page.getByRole('button',{name:'Close tutorial'}).click();await page.reload();await page.getByRole('button',{name:'Replay tutorial',exact:true}).waitFor();assert.equal(await page.getByRole('region',{name:'City tutorial'}).count(),0);
  assert.deepEqual(errors,[]);await writeFile(new URL(`${engineName}-results.json`,out),JSON.stringify({passed:true,aiRequests:requests,errors},null,2));console.log(engineName+': HQ, chat, campaigns, ads, notes, mobile and close-to-city passed');
 }finally{await browser.close();}
}
