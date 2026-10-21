/* Run only against tests/camera_redesign_fixture.py. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.MEOWKEEPER_PLAYWRIGHT||'playwright');
const origin=process.argv[2]||'http://127.0.0.1:18987';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Loopback fixture required');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addInitScript(()=>{
      localStorage.setItem('meowkeeper-preview-mode:live','energy');
      localStorage.setItem('meowkeeper-preview-mode:overview','energy');
    });
    assert.equal((await context.request.post(origin+'/api/v1/auth/login',{
      data:{username:'fixture-admin',password:'fixture-password-only'}
    })).status(),200);
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    for(const route of ['/dashboard','/live']){
      await page.mouse.move(0,0);
      await page.goto(origin+route);
      const stage=page.locator('.preview-hud-auto-hide');
      await stage.waitFor();
      const hud=stage.locator('.preview-mode-hud');
      const opacity=()=>hud.evaluate(el=>Number(getComputedStyle(el).opacity));
      assert.equal(await opacity(),0,route+' starts unobstructed');
      await stage.hover();
      await page.waitForTimeout(250);
      assert.equal(await opacity(),1);
      await hud.locator('[data-preview-mode="energy"]').click();
      await page.mouse.move(0,0);
      await page.waitForTimeout(1500);
      assert.equal(await opacity(),1,'controls stay visible during two-second grace');
      await page.waitForTimeout(800);
      assert.equal(await opacity(),0,'controls fade after grace');
      assert(await stage.evaluate(el=>[...el.querySelectorAll('.preview-mode-hud,.preview-overlay,.preview-toolbar,.ptz-virtual-pad')]
        .every(control=>Number(getComputedStyle(control).opacity)===0)), 'all information and control groups fade');
      await stage.hover();
      await page.mouse.move(0,0);
      await page.waitForTimeout(1000);
      await stage.hover();
      await page.waitForTimeout(1300);
      assert.equal(await opacity(),1,'reenter cancels pending hide');
      await page.mouse.move(0,0);
      await hud.locator('button').first().focus();
      await page.keyboard.press('Tab');
      await page.waitForTimeout(2300);
      assert.equal(await opacity(),1,'keyboard focus keeps controls visible');
      await page.evaluate(()=>document.activeElement.blur());
      await page.waitForTimeout(2300);
      assert.equal(await opacity(),0,'leaving keyboard focus resumes hide');
    }
    const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    await mobile.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}});
    const touch=await mobile.newPage();
    await touch.goto(origin+'/dashboard');
    await touch.waitForSelector('.preview-hud-auto-hide');
    await touch.waitForTimeout(2300);
    assert.equal(await touch.locator('.preview-mode-hud').evaluate(el=>Number(getComputedStyle(el).opacity)),1,
      'touch devices retain accessible controls without hover');
    assert.deepEqual(errors,[]);
    console.log('passed: dashboard/live hover, 2s grace, fade, reenter, keyboard and touch');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
