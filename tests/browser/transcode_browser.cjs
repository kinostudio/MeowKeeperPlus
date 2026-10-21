const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.MEOWKEEPER_PLAYWRIGHT||'playwright');
const origin=process.argv[2],output=process.argv[3];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw new Error('isolated loopback fixture only');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext();
 try{
  assert.equal((await context.request.post(origin+'/api/v1/auth/login',{data:{username:'fixture-admin',password:'fixture-password-only'}})).status(),200);
  await context.addInitScript(()=>{window.RTCPeerConnection=undefined;const supported=MediaSource.isTypeSupported.bind(MediaSource);MediaSource.isTypeSupported=mime=>!mime.includes('hvc1')&&supported(mime);});
  const first=await context.newPage();await first.goto(origin+'/isolated-h264-browser');
  await first.waitForFunction(()=>document.querySelector('video')?.videoWidth>0,{timeout:35000});
  await first.waitForFunction(()=>document.querySelector('.preview-mode-stats')?.textContent.includes('H.264 相容轉碼'));
  const second=await context.newPage();await second.goto(origin+'/isolated-h264-browser');
  await second.waitForFunction(()=>document.querySelector('video')?.videoWidth>0,{timeout:35000});
  await second.bringToFront();
  const start=await second.evaluate(()=>({frames:document.querySelector('video').getVideoPlaybackQuality().totalVideoFrames,time:performance.now()}));
  await second.waitForTimeout(5000);
  const end=await second.evaluate(()=>{const v=document.querySelector('video');return{frames:v.getVideoPlaybackQuality().totalVideoFrames,time:performance.now(),width:v.videoWidth,height:v.videoHeight,currentTime:v.currentTime,stats:document.querySelector('.preview-mode-stats').textContent};});
  const fps=(end.frames-start.frames)/((end.time-start.time)/1000);assert(fps>8&&fps<=22,JSON.stringify({fps,end}));assert.equal(end.width,320);assert.equal(end.height,180);
  await first.close();await second.close();
  const result={status:'passed',synthetic_only:true,forced_hevc_unsupported:true,fps,width:end.width,height:end.height};
  fs.writeFileSync(output,JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
