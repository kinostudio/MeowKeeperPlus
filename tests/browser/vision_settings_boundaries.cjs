/* Actual settings API persistence via loopback acceptance fixture only. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.MEOWKEEPER_PLAYWRIGHT||'playwright');
const origin=process.argv[2]||'http://127.0.0.1:18988';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Loopback fixture required');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const context=await browser.newContext();
    await context.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/settings/ai');
    await page.waitForFunction(()=>document.getElementById('vision-message').textContent.includes('目前未啟用'));
    const input=(id,value)=>page.locator('#'+id).evaluate((el,value)=>{
      el.value=String(value);el.dispatchEvent(new Event('input',{bubbles:true}));
    },value);
    const save=async(expected=200)=>{
      const [reply]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/v1/vision/settings')&&r.request().method()==='PUT'),
        page.getByRole('button',{name:'保存設定',exact:true}).click()
      ]);
      assert.equal(reply.status(),expected,await reply.text());
      await page.waitForFunction(()=>!document.querySelector('#vision-settings-form button[type=submit]').disabled);
      return reply.json();
    };
    await page.locator('input[name=vision-provider][value=gemini]').check();
    await input('vision-daily-limit',73);
    await input('vision-jpeg-quality',91);
    await input('vision-frame-count',6);
    await page.locator('#vision-scene-references').check();
    let values=(await save()).settings;
    assert.equal(values.daily_request_limit,73);
    assert.equal(values.storyboard_jpeg_quality,91);
    assert.equal(values.storyboard_frame_count,6);
    assert.equal(values.storyboard_include_scene_references,true);
    await page.locator('#vision-model').fill('image-a');
    await input('vision-daily-limit',81);
    await input('vision-jpeg-quality',90);
    values=(await save()).settings;
    assert.equal(values.daily_request_limit,81);
    assert.equal(values.storyboard_jpeg_quality,90);
    await page.locator('#vision-model').fill('image-b');
    await input('vision-daily-limit',222);
    values=(await save()).settings;
    assert.equal(values.daily_request_limit,222);
    assert.equal(values.storyboard_jpeg_quality,82,'unchanged field must use new-model defaults');
    await page.locator('#vision-model').fill('image-a');
    values=(await save()).settings;
    assert.equal(values.daily_request_limit,81);
    assert.equal(values.storyboard_jpeg_quality,90,'returning model restores its own unchanged settings');
    await page.locator('input[name=vision-provider][value=openai_compatible]').check();
    assert.equal(await page.locator('#vision-endpoint').inputValue(),'','Gemini-only URL cleared on service switch');
    await page.locator('#vision-model').fill('');
    await input('vision-daily-limit',87);
    values=(await save()).settings;
    assert.equal(values.daily_request_limit,87);
    assert.equal(values.model_id,'');
    await page.locator('#vision-endpoint').fill('https://example.invalid/v1/chat/completions');
    values=(await save()).settings;
    assert.equal(values.endpoint,'https://example.invalid/v1/chat/completions');
    assert.equal(values.model_id,'','disabled endpoint-only draft is saved');
    await page.locator('#vision-enabled').check();
    await page.locator('#vision-consent').check();
    await save(422);
    await page.locator('#vision-enabled').uncheck();
    await page.locator('input[name=vision-provider][value=gemini]').check();
    await page.locator('#vision-model').fill('image-a');
    await input('vision-daily-limit',93);
    values=(await save()).settings;
    assert.equal(values.daily_request_limit,93,'explicit edit overrides returning-model profile');
    assert.equal(values.storyboard_jpeg_quality,90);
    await page.reload();
    await page.waitForFunction(()=>document.getElementById('vision-daily-limit').value==='93');
    assert.equal(await page.locator('#vision-jpeg-quality').inputValue(),'90');
    await page.locator('#vision-scene-references').check();
    await page.locator('#vision-consent').check();
    await page.locator('#vision-scene-reference-consent').check();
    await page.locator('#vision-model').fill('image-c');
    assert.equal(await page.locator('#vision-consent').isChecked(),false);
    assert.equal(await page.locator('#vision-scene-reference-consent').isChecked(),false);
    assert.deepEqual(errors,[]);
    console.log('passed: same-save provider/model profile edits, returning-model isolation, disabled drafts, enable rejection, reload and both consents reset');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
