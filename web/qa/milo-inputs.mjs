import { chromium, webkit } from 'playwright';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const base = process.env.QA_BASE_URL || 'http://127.0.0.1:5179';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname), 'Local QA only');
const out = new URL('../../qa/runs/milo-inputs/', import.meta.url);
await mkdir(out, {recursive:true});
const image = (await readFile(new URL('../public/assets/milo/avatar.png', import.meta.url))).toString('base64');
for (const [name, engine] of Object.entries({chromium, webkit})) {
  if(process.env.QA_ENGINE && process.env.QA_ENGINE !== name) continue;
  const browser=await engine.launch();
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    page.setDefaultTimeout(15000);
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const login=await page.request.post(base+'/api/auth/login',{headers:{Origin:base},data:{username:'admin',password:(await readFile('/tmp/megaday-qa-password','utf8')).trim()}});assert(login.ok());
    const documents={hq:{tracked:[{sku:'QA-A',name:'Travel mug',mode:'workspace'},{sku:'QA-B',name:'Coffee cup',mode:'workspace'}],active:'workspace:QA-A',worksheets:{},activity:[]},chats:{sessions:[],preferredModel:'qa-model'}};
    await page.route('**/api/workspace/*',route=>{
      const key=new URL(route.request().url()).pathname.split('/').pop();
      if(!(key in documents))return route.continue();
      if(route.request().method()==='PUT')documents[key]=route.request().postDataJSON().data;
      return route.fulfill({json:{revision:1,updatedAt:'2026-10-31T00:00:00Z',data:documents[key]}});
    });
    await page.route('**/api/pm/models',route=>route.fulfill({json:{configured:true,models:['qa-model'],defaultModel:'qa-model'}}));
    let requests=0;
    await page.route('**/api/pm/chat',route=>{
      requests++;const body=route.request().postDataJSON(),session=documents.chats.sessions.find(s=>s.id===body.sessionId);
      assert.equal(body.context.sku,'QA-A');assert.equal(session.context.sku,'QA-A');
      assert.equal(session.messages.at(-1).content,'Review current stock');
      assert.equal(session.messages.at(-1).attachments.length,1);
      session.messages.push({id:crypto.randomUUID(),role:'assistant',content:'QA reply for selected product'});
      return route.fulfill({json:{reply:'QA reply for selected product'}});
    });
    // Browser speech services are stubbed; no microphone or paid AI requests.
    await page.addInitScript(()=>{
      localStorage.setItem('megaday.city.tour.v4','complete');
      globalThis.SpeechRecognition=class {
        constructor(){globalThis.qaSpeech=this;}
        start(){this.started=true;}
        stop(){this.onend?.();}
        abort(){this.aborted=true;}
      };
    });
    await page.goto(base+'/city-demo?district=hq&hq=details');
    const hq=page.getByRole('dialog',{name:'MegaDay HQ',exact:true});await hq.waitFor();
    await hq.getByRole('button',{name:'Open PM assistant',exact:true}).click();
    const chat=page.getByRole('dialog',{name:'Milo',exact:true});await chat.waitFor();
    await chat.locator('.milo-context').getByText('QA-A',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Start voice input'}).click();
    await page.evaluate(()=>globalThis.qaSpeech.onresult({resultIndex:0,results:[{isFinal:true,0:{transcript:'Review current stock'}}]}));
    assert.equal(await page.getByLabel('Message Milo',{exact:true}).inputValue(),'Review current stock');assert.equal(requests,0);
    await page.getByRole('button',{name:'Stop voice input'}).click();
    await page.getByLabel('Message Milo',{exact:true}).evaluate((el,base64)=>{
      const bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0)),data=new globalThis.DataTransfer();
      data.items.add(new File([bytes],'pasted-stock.png',{type:'image/png'}));
      el.dispatchEvent(new globalThis.ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
    },image);
    await page.getByRole('button',{name:'Remove pasted-stock.png'}).waitFor();assert.equal(requests,0);
    await page.screenshot({path:new URL(`${name}-voice-image-draft.png`,out).pathname});
    console.log(`${name}: voice and paste draft checked`);
    await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('QA reply for selected product').waitFor();assert.equal(requests,1);
    await page.getByRole('button',{name:'Select QA-B',exact:true}).click();
    await page.getByRole('button',{name:'Discuss HQ product · QA-B',exact:true}).click();
    await chat.locator('.milo-context').getByText('QA-B',{exact:true}).waitFor();
    assert.equal(await page.getByLabel('Message Milo',{exact:true}).inputValue(),'');assert.equal(requests,1);
    await page.getByRole('button',{name:'Start voice input'}).click();
    await page.evaluate(()=>globalThis.qaSpeech.onerror({error:'not-allowed'}));
    await page.getByText(/Microphone access was denied/).waitFor();
    await page.getByRole('button',{name:'Start voice input'}).click();
    await page.getByRole('button',{name:'Close chat'}).click();
    assert(await page.evaluate(()=>globalThis.qaSpeech.aborted));
    for(const width of [768,390,320]){
      await page.setViewportSize({width,height:900});
      await hq.getByRole('button',{name:'Open PM assistant',exact:true}).click();await chat.waitFor();
      assert(await page.evaluate(()=>globalThis.document.documentElement.scrollWidth<=globalThis.innerWidth+1));
      await page.screenshot({path:new URL(`${name}-chat-${width}.png`,out).pathname});
      await page.getByRole('button',{name:'Close chat'}).click();
    }
    assert.deepEqual(errors,[]);
    console.log(`${name}: tracked context, voice draft/denial/cleanup, real image paste, saved attachment and mobile composer passed`);
  }finally{await browser.close();}
}
