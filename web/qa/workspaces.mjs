/* global document, innerWidth, innerHeight */
import { webkit, chromium } from 'playwright';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const base=process.env.QA_BASE_URL || 'http://127.0.0.1:5179';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const out=new URL('../../qa/runs/workspaces/',import.meta.url).pathname;
await mkdir(out,{recursive:true});
for(const [name,engine] of Object.entries({webkit,chromium})) {
 const browser=await engine.launch({ headless: process.env.QA_HEADED !== "1" });
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(['error','warning'].includes(m.type()))errors.push(m.text());});
  await page.goto(base+'/login');
  await page.getByLabel('Username',{exact:true}).fill('admin');
  await page.getByLabel('Password',{exact:true}).fill(await readFile('/tmp/megaday-qa-password','utf8'));
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.waitForURL('**/city-demo');
  for(const [i,title] of ['Start at HQ','Choose ad platforms','Check the warehouse'].entries()) {
    await page.getByRole('heading',{name:title,exact:true}).waitFor();
    await page.screenshot({path:`${out}${name}-tour-${i}.png`});
    await page.getByRole('region',{name:'City tutorial'}).getByRole('button',{name:i===2?'Done':'Next',exact:true}).click();
  }
  await page.getByRole('button',{name:'Close workspace'}).click();
  await page.getByRole('link',{name:'View city',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.city-canvas')?.dataset.assets==='ready');
  await page.screenshot({path:`${out}${name}-overview.png`});
  for(const district of ['MegaDay HQ','Warehouse','Ads Platform']) {
    await page.getByRole('button',{name:district,exact:true}).click();
    const dialog=page.getByRole('dialog',{name:district,exact:true});
    await dialog.waitFor();
    let box=await dialog.boundingBox();
    assert(box.width>=900 && box.width<=1328, 'Default workspace is too narrow');
    const grip=dialog.getByRole('separator',{name:'Resize panel'});
    const handle=await grip.boundingBox();
    await page.mouse.move(handle.x+5,handle.y+handle.height/2);
    await page.mouse.down();await page.mouse.move(handle.x+125,handle.y+handle.height/2,{steps:8});await page.mouse.up();
    const resized=await dialog.boundingBox();assert(resized.width<box.width-80,'Mouse resize did not shrink');
    await grip.focus();await page.keyboard.press('End');
    assert((await dialog.boundingBox()).width>resized.width,'Keyboard resize failed');
    await page.keyboard.press('Home');
    const restored=(await dialog.boundingBox()).width;
    await page.getByRole('button',{name:'Expand workspace'}).click();
    assert((await dialog.boundingBox()).width>restored+100);
    await page.getByRole('button',{name:'Restore workspace'}).click();
    assert.equal(Math.round((await dialog.boundingBox()).width),Math.round(restored));
    // Restore the roomy default for the next workplace.
    await grip.focus();await page.keyboard.press('End');
    await page.getByRole('button',{name:'Close workspace'}).click();
    await page.getByRole('link',{name:'View city',exact:true}).click();
  }
  await page.getByRole('button',{name:'Visit MegaDay Twin'}).click();
  await page.getByRole('dialog',{name:'MegaDay HQ',exact:true}).waitFor();
  await page.getByRole('button',{name:'Close workspace'}).click();
  await page.getByRole('link',{name:'View city',exact:true}).click();
  await page.getByRole('button',{name:'Open PM assistant',exact:true}).first().click();
  await page.getByRole('textbox',{name:'Message Milo'}).fill('Keep this draft');
  await page.getByRole('tab',{name:/Updates/}).click();
  await page.getByRole('button',{name:'Close chat'}).click();
  await page.locator('.pm-launcher').click();
  assert.equal(await page.getByRole('textbox',{name:'Message Milo'}).inputValue(),'Keep this draft');
  await page.screenshot({path:`${out}${name}-milo.png`});
  await page.getByRole('button',{name:'Close chat'}).click();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Replay tutorial'}).click();
  for(let i=0;i<3;i++) {
    const region=page.getByRole('region',{name:'City tutorial'});
    const box=await region.boundingBox();assert(box.x>=0&&box.y>=0&&box.x+box.width<=390&&box.y+box.height<=844);
    await page.screenshot({path:`${out}${name}-tour-mobile-${i}.png`});
    await region.getByRole('button',{name:i===2?'Done':'Next',exact:true}).click();
  }
  await page.getByRole('button',{name:'Close workspace'}).click();
  await page.getByRole('link',{name:'View city',exact:true}).click();
  await page.getByRole('button',{name:'Open PM assistant',exact:true}).first().click();
  await page.getByRole('textbox',{name:'Message Milo'}).waitFor();
  await page.screenshot({path:`${out}${name}-milo-mobile.png`});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
  assert.deepEqual(errors,[]);
  console.log(name+': automatic panels, three-place tutorial, mouse/keyboard resizing, expand/restore, direct Milo chat, mobile passed');
 } finally {await browser.close();}
}
