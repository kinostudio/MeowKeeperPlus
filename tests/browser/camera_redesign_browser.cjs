/* Run against tests/camera_redesign_fixture.py, never a production Dashboard. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.MEOWKEEPER_PLAYWRIGHT||'playwright');
const origin=process.argv[2]||'http://127.0.0.1:18979';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw new Error('Only an isolated loopback fixture is accepted');
const output=path.resolve(__dirname,'../../reports/camera-ui-redesign-2026-10-18');fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:810},reducedMotion:'reduce'});
 const login=await context.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}});assert.equal(login.status(),200);
 const page=await context.newPage(),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 async function screenshot(name){await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});}
 async function fit(label){const sizes=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert(sizes.scroll<=sizes.width+1,`${label} overflow ${JSON.stringify(sizes)}`);checks.push(label);}
 try{
  await page.goto(origin+'/dashboard');await page.waitForSelector('.hardware-gauge[data-available="true"]');
  await page.waitForFunction(()=>document.getElementById('overview-frame')?.naturalWidth>0);
  assert.equal(await page.locator('.monitor-hero .hardware-gauge').count(),5);
  assert.equal(await page.locator('.monitor-hero .hero-services .card').count(),3);
  const heroGeometry=await page.evaluate(()=>({bottom:document.querySelector('.monitor-hero').getBoundingClientRect().bottom,banner:document.querySelector('body>p.notice')?.getBoundingClientRect().bottom||0,height:innerHeight}));
  assert(heroGeometry.bottom-heroGeometry.banner<=heroGeometry.height,`First screen exceeds viewport: ${JSON.stringify(heroGeometry)}`);checks.push('hardware preview and three services fit desktop first screen excluding explicit test banner');
  await screenshot('dashboard-1440');await fit('dashboard 1440');
  const ring=await page.locator('[data-metric="system_gpu_percent"] .gauge-value').innerText();assert(ring.includes('15%'));
  await page.locator('[data-save-capture="photo"]').first().click();await page.waitForSelector('.capture-toast:not([hidden])');
  await page.waitForFunction(()=>document.getElementById('capture-result')?.textContent.includes('3840×2160'));
  assert(await page.locator('#overview-preview #capture-result').count());assert((await page.locator('#capture-result').textContent()).includes('3840×2160'));
  assert.equal(await page.locator('.capture-toast button').count(),0,'capture snackbar must not require or offer a close action');
  assert.equal(await page.locator('.capture-toast').getAttribute('role'),'status');
  await screenshot('capture-snackbar');
  await page.waitForSelector('.capture-toast[hidden]',{timeout:7000});checks.push('capture snackbar auto-dismisses without blocking preview or requiring a click');
  const flicker=await page.evaluate(async()=>{const img=document.getElementById('overview-frame');let hidden=0;const observer=new MutationObserver(records=>{for(const record of records)if(record.attributeName==='hidden'&&img.hidden)hidden++;});observer.observe(img,{attributes:true});await new Promise(r=>setTimeout(r,1500));observer.disconnect();return hidden;});assert.equal(flicker,0);checks.push('energy image has no hide/show flicker');
  await page.goto(origin+'/live');await page.waitForSelector('#ptz-coordinate-map[aria-disabled="false"]');
  await page.locator('[data-preview-mode="energy"]').click();await page.waitForFunction(()=>document.getElementById('preview-image')?.naturalWidth>0);
  const map=page.locator('#ptz-coordinate-map');await map.scrollIntoViewIfNeeded();const box=await map.boundingBox();
  await map.click({position:{x:box.width*.55,y:box.height*.40}});
  await page.waitForFunction(()=>document.getElementById('live-ptz-message').textContent.includes('到達選取位置'));
  assert.equal(await page.locator('#ptz-position-hud').isVisible(),true);
  assert.equal(await page.locator('#ptz-coordinate-map .ptz-target-dot').getAttribute('visibility'),'visible');
  await map.focus();await page.keyboard.press('ArrowUp');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.getElementById('live-ptz-message').textContent.includes('到達選取位置'));
  await screenshot('live-position');checks.push('point click and keyboard issue absolute move with real fixture readback');
  await page.locator('[data-save-capture="video"]').click();await page.waitForSelector('.capture-toast:not([hidden])');
  assert.equal(await page.locator('.capture-toast button').count(),0);
  assert(await page.locator('.preview-stage #capture-result').count());await page.locator('#preview-fullscreen').click();
  await page.waitForFunction(()=>Boolean(document.fullscreenElement));assert(await page.locator('.capture-toast').isVisible());
  await screenshot('fullscreen-capture');await page.keyboard.press('Escape');
  await page.goto(origin+'/cameras/xiaomi/setup');await page.waitForSelector('.camera-device');
  assert.equal(await page.locator('.wizard-step').count(),3);assert.equal(await page.locator('#camera-remote-pair').count(),1);
  await page.locator('#xiaomi-connect').click();await page.waitForFunction(()=>document.getElementById('xiaomi-message').textContent.includes('正在接收新的攝影機畫面'));
  await page.locator('[data-camera-role="provider"]').click();await page.waitForFunction(()=>document.querySelector('#relay-peer').options.length>1);
  assert.equal(await page.locator('#camera-provider-options').isVisible(),true);await screenshot('camera-provider');
  await page.locator('[data-camera-role="monitor"]').click();await screenshot('camera-wizard');
  for(const width of [360,390,768,1440,1920]){
   await page.setViewportSize({width,height:width<800?844:1080});
   for(const route of ['/dashboard','/live','/cameras/xiaomi/setup','/events']){
    await page.goto(origin+route);await page.waitForTimeout(300);await fit(`${route} ${width}`);
   }
  }
  await page.setViewportSize({width:720,height:405});await page.goto(origin+'/dashboard');await fit('200% equivalent CSS viewport');await screenshot('dashboard-200pct-reflow');
  await page.waitForSelector('.hardware-gauge[data-available="true"]');
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('meowkeeper:status',{detail:{metrics:{telemetry:{sampled_at:Date.now()/1000-60,system_cpu_percent:99}}}})));
  assert.equal(await page.locator('[data-metric="system_cpu_percent"]').getAttribute('data-available'),'false');assert(!(await page.locator('[data-metric="system_cpu_percent"] .gauge-value').innerText()).includes('99'));checks.push('expired hardware samples do not masquerade as current readings');
  const touch=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  try{await touch.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}});const mobile=await touch.newPage();mobile.on('pageerror',e=>errors.push(e.message));await mobile.goto(origin+'/live');const target=mobile.locator('#ptz-coordinate-map');await mobile.waitForSelector('#ptz-coordinate-map[aria-disabled="false"]');await target.scrollIntoViewIfNeeded();const bounds=await target.boundingBox();await mobile.touchscreen.tap(bounds.x+bounds.width*.52,bounds.y+bounds.height*.47);await mobile.waitForFunction(()=>document.getElementById('live-ptz-message').textContent.includes('到達選取位置'));checks.push('touch coordinate selection produces verified fixture readback');await mobile.screenshot({path:path.join(output,'live-touch-390.png')});}finally{await touch.close();}
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({status:'passed',fixture_only:true,checks,page_errors:errors},null,2));
  console.log(JSON.stringify({status:'passed',checks:checks.length,page_errors:errors,output}));
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
