/* Real browser + synthetic HEVC/AAC loopback only; never a household camera. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.MEOWKEEPER_PLAYWRIGHT || 'playwright');
const origin = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) throw Error('isolated loopback fixture required');
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  try {
    await context.addInitScript(() => {
      window.RTCPeerConnection = undefined;
      const supported = MediaSource.isTypeSupported.bind(MediaSource);
      MediaSource.isTypeSupported = mime => !mime.includes('hvc1') && supported(mime);
      localStorage.setItem('meowkeeper-preview-mode:overview','smooth');
      localStorage.setItem('meowkeeper-preview-mode:live','smooth');
    });
    assert.equal((await context.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}})).status(),200);
    let captures=0, profiles=0, opens=0, closes=0;
    await context.route('**/api/v1/cameras/xiaomi/devices',route=>route.fulfill({json:{items:[{internal_id:'synthetic',display_name:'隔離攝影機',selected:true,
      quality_switch_available:true,audio_reconnect_available:true,source_audio_enabled:false,selected_profile:'subtype=1',ptz:{enabled:false}}]}}));
    await context.route('**/api/v1/cameras/xiaomi/devices/synthetic/privacy',route=>route.fulfill({json:{mode:'position',standby_available:false}}));
    await context.route('**/api/v1/cameras/xiaomi/devices/synthetic/profile',async route=>{
      profiles++;const body=route.request().postDataJSON();assert.equal(body.audio_enabled,true);assert.equal(body.confirm_reconnect,true);
      await route.fulfill({json:{success:true,video_verified:true,audio_verified:false,message_zh_tw:'已要求音訊，但尚未驗證音軌；不能宣稱收音成功。'}});
    });
    await context.route('**/api/v1/live/save', async route => {
      captures++;
      await route.fulfill({json:{status:'saved',message_zh_tw:'已保存隔離測試截圖'}});
    });
    const page=await context.newPage(), errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('websocket',socket=>{if(socket.url().includes('/preview/mse')){opens++;socket.on('close',()=>closes++);}});
    await page.goto(origin+'/dashboard');
    await page.waitForFunction(()=>document.querySelector('.preview-video')?.videoWidth>0,{timeout:35000});
    await page.evaluate(()=>{window.testVideo=document.querySelector('.preview-video');window.testDocument=document;window.testSource=window.testVideo.src;});
    const stage=()=>page.locator('[data-monitor-view]:not([hidden]) .overview-preview, [data-monitor-view]:not([hidden]) .preview-stage');
    await stage().hover();
    await stage().locator('[data-preview-control="audio"]').click();
    await page.waitForFunction(()=>document.querySelector('.preview-audio-toggle')?.dataset.audioState==='track');
    const audio=await page.evaluate(()=>({muted:window.testVideo.muted,decodedBytes:window.testVideo.webkitAudioDecodedByteCount||0,audioTracks:window.testVideo.captureStream?.().getAudioTracks().length||0}));
    assert.equal(audio.muted,false);
    assert(audio.decodedBytes>0||audio.audioTracks>0,'real MSE decoder must expose audio, not just an enabled icon');
    const before=await page.evaluate(()=>window.testVideo.getVideoPlaybackQuality().totalVideoFrames);
    for(let index=0;index<8;index++){
      const live=index%2===0;
      await page.locator(`#primary-nav a[href="${live?'/live':'/'}"]`).click();
      await page.waitForFunction(scope=>!document.querySelector(`[data-monitor-view="${scope}"]`).hidden,live?'live':'overview');
      assert(await page.evaluate(()=>document===window.testDocument&&document.querySelector('.preview-video')===window.testVideo&&window.testVideo.src===window.testSource));
      assert.equal(await page.locator('.preview-video').count(),1);
      assert.equal(await page.locator('.preview-mode-hud').count(),1);
      assert.equal(await page.evaluate(()=>window.testVideo.muted),false);
      assert.equal(opens,1,'route transitions must retain the actual WebSocket');
      assert.equal(closes,0);
    }
    await page.waitForTimeout(1200);
    assert(await page.evaluate(start=>window.testVideo.getVideoPlaybackQuality().totalVideoFrames>start,before));
    await page.goBack();
    assert(await page.evaluate(()=>document===window.testDocument&&document.querySelector('.preview-video')===window.testVideo));
    assert.equal(opens,1);
    await stage().hover();
    await page.locator('[data-monitor-view]:not([hidden]) [data-save-capture="photo"]').click();
    await page.waitForTimeout(200);
    assert.equal(captures,1,'one capture click must not run both page handlers');
    const ids=await page.locator('[id]').evaluateAll(nodes=>nodes.map(node=>node.id));
    assert.equal(new Set(ids).size,ids.length,'combined shell keeps IDs unique');
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile shell must not overflow horizontally');
    await stage().hover();
    await stage().locator('[data-preview-mode="sleep"]').click();
    await page.waitForTimeout(500);
    const sleepingOpens=opens;
    await page.locator('#primary-nav a[href="/"]').click();
    await page.locator('#primary-nav a[href="/live"]').click();
    assert.equal(opens,sleepingOpens,'navigation must not wake sleeping playback');
    assert.equal(await page.locator('.preview-sleep-state:not([hidden])').count(),1);
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#live-source-audio').click();
    assert.equal(profiles,0,'cancelled source-audio confirmation must not mutate the source');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#live-source-audio').click();
    await page.waitForFunction(()=>document.getElementById('live-quality-help').textContent.includes('尚未驗證音軌'));
    assert.equal(profiles,1,'source-audio opt-in uses one explicitly confirmed profile request');
    assert.equal(opens,sleepingOpens,'source change must not wake a sleeping preview');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',synthetic_only:true,transitions:8,media_sockets:opens,media_closes:closes,audio,capture_requests:captures,confirmed_source_audio_requests:profiles,desktop:1440,mobile:390}));
  } finally {await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
