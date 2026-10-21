/* Real loopback app + offline user clip. Never target a production Host. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.MEOWKEEPER_PLAYWRIGHT || 'playwright');
const origin = process.argv[2] || 'http://127.0.0.1:18986';
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw Error('Only loopback acceptance fixture allowed');
const output = path.resolve(__dirname, '../../data/diagnostics/vision-overlay-acceptance-20261020');
fs.mkdirSync(output, {recursive:true});

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const errors = [], checks = [];
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    await context.addInitScript(() => {
      localStorage.setItem('meowkeeper-preview-mode:live','energy');
      localStorage.setItem('meowkeeper-preview-mode:overview','energy');
    });
    const login = await context.request.post(origin + '/api/v1/auth/login', {
      data:{username:'fixture-admin',password:'fixture-password-only'}
    });
    assert.equal(login.status(),200);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/settings/ai');
    await page.waitForFunction(() => document.getElementById('vision-message').textContent.includes('目前未啟用'));
    assert.equal(await page.locator('script[src*="/static/js/vision-settings.js"]').count(),1);
    checks.push('Actual template loads vision module once and reads real backend settings');
    await page.locator('input[name="vision-provider"][value="gemini"]').check();
    await page.locator('#vision-model').fill('acceptance-image-model');
    const save = () => Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/v1/vision/settings') && r.request().method()==='PUT'),
      page.getByRole('button',{name:'保存設定',exact:true}).click()
    ]).then(([r]) => { assert.equal(r.status(),200); return r.json(); });
    const saved = await save();
    assert.equal(saved.settings.model_id,'acceptance-image-model');
    assert.equal(saved.settings.endpoint,'https://generativelanguage.googleapis.com/v1beta/models/acceptance-image-model:generateContent');
    await page.reload();
    await page.waitForFunction(() => document.getElementById('vision-model').value==='acceptance-image-model');
    checks.push('UI save reaches real API and persists across reload without a key or external request');
    await page.locator('#vision-enabled').check();
    await page.locator('#vision-consent').check();
    assert.equal((await save()).settings.enabled,true);
    await page.locator('#vision-enabled').uncheck();
    assert.equal((await save()).settings.enabled,false);
    checks.push('Explicit enable/consent and disable persist in isolated store');
    let puts=0;
    const countPut=r=>{if(r.method()==='PUT'&&r.url().endsWith('/api/v1/vision/settings')) puts++;};
    page.on('request',countPut);
    await page.locator('#vision-model').fill('Gemini display name with spaces');
    await page.getByRole('button',{name:'保存設定',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('vision-message').textContent.includes('模型 ID'));
    assert.equal(puts,0);
    page.off('request',countPut);
    await page.locator('#vision-model').fill('acceptance-image-model');
    await save();
    await page.locator('.vision-center').screenshot({path:path.join(output,'vision-settings-saved.png')});
    checks.push('Display name rejected with actionable message before network submission');
    for (const width of [390,768,1440]) {
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth+1),`settings overflow ${width}`);
    }
    await page.setViewportSize({width:1440,height:1000});
    for (const route of ['/live','/dashboard']) {
      await page.goto(origin + route);
      await page.waitForSelector('.preview-ai-box',{timeout:60000});
      const stage=page.locator('.preview-ai-layer').locator('..');
      assert.equal(await stage.locator('img').first().evaluate(image=>image.naturalWidth),960,
        'Preview must use the real resized clip frame, not the synthetic fixture background');
      await stage.getByRole('button',{name:'身分候選',exact:true}).click();
      await stage.getByRole('button',{name:'動作候選',exact:true}).click();
      const label=await page.locator('.preview-ai-box span').first().innerText();
      assert(label.includes('貓 1') && label.includes('身分候選：未知') && label.includes('動作候選：尚未判斷'));
      const geometry=await page.locator('.preview-ai-box').first().evaluate(box=>{
        const b=box.getBoundingClientRect(),p=box.parentElement.getBoundingClientRect();
        const label=box.querySelector('span').getBoundingClientRect();
        return {valid:b.width>0&&b.height>0&&b.left>=p.left-1&&b.top>=p.top-1&&b.right<=p.right+1&&b.bottom<=p.bottom+1,
          labelOutside:label.bottom<=b.top+3||label.top>=b.bottom-3};
      });
      assert(geometry.valid,'actual model bbox lies within preview');
      assert(geometry.labelOutside,'candidate labels must not cover the detection target');
      await stage.screenshot({path:path.join(output,route==='/live'?'live-event-overlay.png':'dashboard-event-overlay.png')});
      for(const name of ['偵測框','身分候選','動作候選']) await stage.getByRole('button',{name,exact:true}).click();
      assert.equal(await page.locator('.preview-ai-box').count(),0);
      // Restore defaults, so each page starts with only boxes enabled.
      await stage.getByRole('button',{name:'偵測框',exact:true}).click();
      checks.push(`${route}: actual offline clip YOLO → status API → shared preview → visible bbox, labels and toggles`);
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'browser-result.json'),JSON.stringify({status:'passed',checks,page_errors:errors,
      limits:['Offline event replay, not live C701','Identity and behavior remain unconfirmed','No cloud or Telegram calls']},null,2));
    console.log(JSON.stringify({status:'passed',checks,output}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
