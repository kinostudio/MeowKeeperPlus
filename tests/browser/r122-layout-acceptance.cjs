const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.MEOWKEEPER_PLAYWRIGHT);
const origin='http://127.0.0.1:18984',output=path.resolve(__dirname,'../../../r122-browser-results');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const errors=[],checks=[],issues=[];
 try{
  assert.equal((await context.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}})).status(),200);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  for(const width of [360,390,768,1440,1920]){
   await page.setViewportSize({width,height:width<800?844:1080});
   for(const route of ['/dashboard','/live','/events','/settings','/settings/ai','/settings/pets','/settings/telegram','/settings/source']){
    const response=await page.goto(origin+route);assert.equal(response.status(),200,route);
    await page.waitForTimeout(350);
    const dialog=page.locator('#home-guide');if(await dialog.evaluate(e=>e.open))await page.locator('[data-guide-close]').click();
    const geometry=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    if(geometry.scroll>geometry.width+1)issues.push({route,width,...geometry});
    if(width<761)assert(await page.locator('.bottom-tabs').isVisible(),route);
    if(route==='/settings/ai'){
     const tips=page.locator('.field-tip-button:visible');
     for(let i=0;i<await tips.count();i++){
      await tips.nth(i).focus();
      const fit=await page.locator('.field-tip-anchor.open .field-tip').evaluateAll(es=>es.every(e=>{const b=e.getBoundingClientRect();return b.left>=-1&&b.right<=innerWidth+1}));
      assert(fit,'open tooltip exceeds viewport '+width);
     }
     await page.locator('h2').first().click();
    }
    if(route==='/settings')assert.equal(await page.locator('#set-notify .set-card-state').innerText(),'已啟用，待確認');
    if([390,1440].includes(width)&&['/live','/events','/settings','/settings/ai'].includes(route))await page.screenshot({path:path.join(output,route.replaceAll('/','_')+'-'+width+'.png'),fullPage:false});
    checks.push(route+' '+width);
   }
  }
  await page.setViewportSize({width:1440,height:900});await page.goto(origin+'/live');
  await page.locator('[data-guide-open]').click();assert(await page.locator('#home-guide').evaluate(e=>e.open));
  await page.keyboard.press('Escape');assert(!(await page.locator('#home-guide').evaluate(e=>e.open)));checks.push('guide opens and Escape closes');
  const controls=await page.locator('#preview-ptz-directions button').count();assert(controls>=4);
  await page.route('**/api/v1/events?*',r=>r.fulfill({json:{items:[
   {event_id:'synthetic-one',event_type:'cat_detected',trigger_at:new Date().toISOString(),pet_id:'Test A',clip_exists:true,has_human_review:false,telegram_status:'sent'},
   {event_id:'synthetic-two',event_type:'cat_detected',trigger_at:'2020-01-01T00:00:00Z',pet_id:'Test B',clip_exists:false,has_human_review:true,telegram_status:'failed'}
  ]}}));
  await page.goto(origin+'/events');await page.waitForSelector('#events-page-list .event-row');
  assert.equal(await page.locator('#events-page-list .event-row').count(),2);
  await page.locator('[data-events-view="pending"]').click();assert.equal(await page.locator('#events-page-list .event-row').count(),1);
  await page.locator('#events-page-list button').filter({hasText:'播放'}).click();assert(await page.locator('#event-playback-dialog').evaluate(e=>e.open));
  await page.locator('#event-playback-close').click();checks.push('synthetic events filter and playback dialog');
  await page.locator('[data-events-view="all"]').click();
  for(const width of [1440,390]){await page.setViewportSize({width,height:width<800?844:900});await page.screenshot({path:path.join(output,'_events-'+width+'.png')});}
  let restoreHeader='';
  await page.route('**/api/v1/scene-registry',r=>r.fulfill({json:{views:[{view_id:'synthetic-view',name:'Test spot',position_binding:{preset_label:'Test spot'}}]}}));
  await page.route('**/api/v1/scene-registry/views/synthetic-view/restore-position',r=>{restoreHeader=r.request().headers()['x-meowkeeper-csrf'];return r.fulfill({json:{status:'aligned',message_zh_tw:'synthetic readback'}});});
  await page.goto(origin+'/live');await page.locator('#live-spots-list button').click();
  await page.waitForFunction(()=>document.querySelector('#live-spots-message').textContent.includes('synthetic readback'));assert(restoreHeader);checks.push('saved spot POST includes CSRF; synthetic response only');
  await page.locator('[data-preview-mode="energy"]').click();
  await page.waitForFunction(()=>document.querySelector('#preview-image').naturalWidth>0);
  for(const width of [1440,390]){await page.setViewportSize({width,height:width<800?844:900});await page.screenshot({path:path.join(output,'_live-'+width+'.png')});}
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({fixture_only:true,checks,issues,page_errors:errors},null,2));
  console.log(JSON.stringify({checks:checks.length,issues,page_errors:errors,output}));
  assert.deepEqual(issues,[]);assert.deepEqual(errors,[]);
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
