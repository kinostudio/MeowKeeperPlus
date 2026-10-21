const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.MEOWKEEPER_PLAYWRIGHT || 'playwright');
const root=path.resolve(__dirname,'../..');
const partial=fs.readFileSync(path.join(root,'src/meowkeeper/app/web/templates/_cpu_temperature_setup.html'),'utf8').replace(/<script[\s\S]*?<\/script>/,'');
async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.MEOWKEEPER_EDGE||'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844}});
    let local=true,available=true,posts=0;
    await page.route('http://fixture.invalid/**',async route=>{
      if(route.request().url().includes('/api/')){
        if(route.request().method()==='POST'){
          posts++;assert.equal(route.request().headers()['x-meowkeeper-csrf'],'fixture-only');
          await route.fulfill({json:{state:'running',message_zh_tw:'請在監控主機完成授權；尚未確認測溫成功。'}});
        }else await route.fulfill({json:{state:'idle',local_request:local,launcher_available:available,reason_zh_tw:available?'':'完整性未通過，未開啟安裝。'}});
      }else await route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><main>'+partial+'</main>'});
    });
    await page.context().addCookies([{name:'meowkeeper_csrf',value:'fixture-only',url:'http://fixture.invalid'}]);
    async function load(){
      await page.goto('http://fixture.invalid/analytics#cpu-temperature-setup');
      await page.addStyleTag({path:path.join(root,'src/meowkeeper/app/web/static/style.css')});
      await page.addScriptTag({path:path.join(root,'src/meowkeeper/app/web/static/js/cpu-temperature-setup.js')});
    }
    await load();
    const button=page.locator('#cpu-setup-launch');
    await assert.doesNotReject(()=>button.waitFor({state:'visible'}));
    await page.waitForFunction(()=>!document.getElementById('cpu-setup-launch').disabled);
    assert.equal(posts,0,'loading must never install');
    page.once('dialog',d=>d.dismiss());await button.click();assert.equal(posts,0,'cancel must never launch');
    page.once('dialog',d=>d.accept());await button.click();
    await page.locator('#cpu-setup-message').filter({hasText:'尚未確認測溫成功'}).waitFor();
    assert.equal(posts,1);assert.equal(await button.isDisabled(),true);
    local=false;await load();
    await page.locator('#cpu-setup-message').filter({hasText:'這是遠端連線'}).waitFor();
    assert.equal(await button.isDisabled(),true);
    local=true;available=false;await load();
    await page.locator('#cpu-setup-message').filter({hasText:'完整性未通過'}).waitFor();
    assert.equal(await button.isDisabled(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    console.log(JSON.stringify({status:'passed',fixture_only:true,page_load_posts:0,explicit_posts:posts,remote_blocked:true,invalid_package_blocked:true,mobile_overflow:false}));
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
