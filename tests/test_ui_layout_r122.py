"""r122: live / events / settings / phone layouts follow the design without dropping existing controls."""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

WEB = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"


def render(name: str, page_path: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template(name).render(page_path=page_path, page_title="測試", term_help_terms={})


class LiveLayoutTests(unittest.TestCase):
    def test_camera_controls_sit_in_side_column_and_keep_every_control(self):
        html = render("live.html", "/live")
        side = html[html.index('<aside class="live-side"'):html.index("</aside>")]
        for control in ("preview-ptz-directions", "ptz-coordinate-map", "ptz-refresh", "live-ptz-controls",
                        "live-spots", "privacy-mode"):
            self.assertIn(f'id="{control}"', side)
        for action in ("hide", "show", "capture", "set_mode"):
            self.assertIn(f'data-privacy-action="{action}"', side)
        main = html[html.index('<section class="live-main"'):html.index('<aside class="live-side"')]
        for control in ("preview-image", "ptz-position-hud", "live-quality-profile", "live-quality-apply", "capture-result"):
            self.assertIn(f'id="{control}"', main)
        self.assertEqual(main.count("data-save-capture="), 2)

    def test_quick_quality_buttons_drive_existing_select(self):
        html = render("live.html", "/live")
        for choice in ("low", "auto", "high"):
            self.assertIn(f'data-quality-choice="{choice}"', html)
        self.assertIn("apply.click()", html)

    def test_saved_spots_use_verified_restore_endpoint(self):
        html = render("live.html", "/live")
        self.assertIn("/restore-position", html)
        self.assertIn("X-MeowKeeper-CSRF", html)
        self.assertIn("position_binding", html)  # only views with a verified bound position


class EventsLayoutTests(unittest.TestCase):
    def test_filters_pending_and_table(self):
        html = (TEMPLATES / "events.html").read_text(encoding="utf-8")
        for view in ("all", "pending", "today"):
            self.assertIn(f'data-events-view="{view}"', html)
        for element in ("events-pending", "events-pending-list", "events-page-list", "events-kind-filter",
                        "events-previous", "events-next", "event-playback-dialog"):
            self.assertIn(f'id="{element}"', html)
        self.assertIn('class="events-table-head"', html)

    def test_rows_offer_play_download_and_review(self):
        js = (STATIC / "js" / "event-list.js").read_text(encoding="utf-8")
        self.assertIn("openPlayback(item)", js)
        self.assertIn('download.setAttribute("download","")', js)
        self.assertIn('"meowkeeper:review"', js)
        self.assertIn("has_human_review", js)
        for state in ("ok", "warn", "alert", "off"):
            self.assertIn(f'["{state}"', js)


class SettingsLayoutTests(unittest.TestCase):
    def test_overview_route_and_cards(self):
        api = (WEB / "api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/settings", response_class=HTMLResponse)', api)
        html = render("settings.html", "/settings")
        for key in ("camera", "notify", "cats", "ai", "remote", "computer"):
            self.assertIn(f'id="set-{key}"', html)
        self.assertIn("/static/js/settings-overview.js", html)
        base = render("base.html", "/settings")
        self.assertIn('href="/settings" title="設定" class="nav-primary active"', base)

    def test_ai_form_keeps_every_field_inside_one_form(self):
        html = render("settings.html", "/settings/ai")
        form = html[html.index('<form id="ai-settings-form"'):html.index("</form>", html.index('<form id="ai-settings-form"'))]
        fields = re.findall(r'id="(ai-[a-z0-9-]+)"', (TEMPLATES / "settings.html").read_text(encoding="utf-8"))
        js = (STATIC / "js" / "ai.js").read_text(encoding="utf-8")
        used = set(re.findall(r'"(ai-[a-z0-9-]+)"', js))
        for field in used & set(fields):
            if field.startswith(("ai-adaptive", "ai-runtime", "ai-clip", "ai-message")):
                continue
            self.assertIn(f'id="{field}"', form, field)
        self.assertIn('id="ai-clip-bar"', form)
        self.assertIn("settings-savebar", form)

    def test_sub_pages_link_back_to_overview(self):
        self.assertIn('href="/settings">← 設定', render("settings.html", "/settings/telegram"))
        self.assertIn('href="/settings">← 設定', render("diagnostics.html", "/diagnostics"))
        self.assertNotIn("← 設定", render("settings.html", "/settings/pets"))


class PhoneLayoutTests(unittest.TestCase):
    def test_bottom_tabs_replace_sidebar_on_phones(self):
        html = render("base.html", "/events")
        nav = html[html.index('<nav class="bottom-tabs"'):html.index("</nav>", html.index('<nav class="bottom-tabs"'))]
        self.assertEqual(re.findall(r"<span>([^<]+)</span></a>", nav), ["首頁", "即時", "事件", "貓咪", "設定"])
        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertLess(html.index('<nav class="bottom-tabs"'), html.index("</body>"))
        self.assertNotIn("bottom-tabs", html[html.index('<dialog id="home-guide"'):html.index("</dialog>")])
        css = (STATIC / "css" / "warm.css").read_text(encoding="utf-8")
        phone = css[css.index("/* ===== 手機版 (r122)"):]
        self.assertIn(".sidebar{display:none!important}", phone)
        self.assertIn(".bottom-tabs{position:fixed", phone)

    def test_unique_ids_on_new_pages(self):
        for name, path in (("live.html", "/live"), ("events.html", "/events"), ("settings.html", "/settings"),
                           ("settings.html", "/settings/ai")):
            html = render(name, path)
            found = re.findall(r'\bid="([^"]+)"', html)
            self.assertEqual(len(found), len(set(found)), path)


class RuntimeRegressionTests(unittest.TestCase):
    def run_node(self, program, script):
        root = WEB.parents[3]
        result = subprocess.run(
            [str(root / "vendor/javascript/node.exe"), "-e", program, str(script)],
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_notification_needs_positive_delivery_evidence(self):
        self.run_node(r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert');
const cards={},listeners={};
const card=id=>cards[id]??=( {dataset:{},parts:{},querySelector(s){return this.parts[s]??={dataset:{},textContent:''}}} );
const document={querySelector:()=>({}),getElementById:card,addEventListener:(n,f)=>listeners[n]=f};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,fetch:async()=>({ok:true,json:async()=>({items:[]})})});
function update(t){listeners['meowkeeper:status']({detail:{status:'running',metrics:{telegram:t,ai:{fps:3}}}});return cards['set-notify'].querySelector('.set-card-state').textContent;}
for(const success_count of [undefined,null,0,'2'])assert.equal(update({enabled:true,success_count}),'已啟用，待確認');
assert.equal(update({enabled:true,success_count:2}),'已成功傳送');
assert.equal(update({enabled:true,status:'failed',success_count:2}),'傳送失敗');
assert.equal(update({enabled:false,success_count:2}),'未啟用');
assert(!cards['set-ai'].querySelector('.set-card-detail').textContent.includes('事件錄影開啟'));
''', STATIC / "js/settings-overview.js")

    def test_high_quality_includes_three_and_prefers_verified_profile(self):
        self.run_node(r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert');
const text=fs.readFileSync(process.argv[1],'utf8');
const start=text.indexOf('(() => {',text.indexOf('/* Quick quality'));
const script=text.slice(start,text.indexOf('  const list =',start))+'})();';
for(const [values,verified,wanted] of [[['subtype=3'],null,'subtype=3'],[['subtype=2','subtype=3'],null,'subtype=3'],[['subtype=2','subtype=3'],'subtype=2','subtype=2'],[['subtype=1','subtype=2','subtype=3'],'subtype=1','subtype=3']]){
 const select={options:values.map(value=>({value,dataset:{verifiedHighest:String(value===verified)}})),value:'',disabled:false,addEventListener(){},dispatchEvent(){}};
 let applied=0,listener;
 const apply={disabled:false,click(){applied++}},button={dataset:{qualityChoice:'high'},setAttribute(){}};
 const seg={querySelectorAll:()=>[button],addEventListener(n,f){listener=f}};
 vm.runInNewContext(script,{document:{getElementById:id=>({'live-quality-profile':select,'live-quality-apply':apply,'live-quality-seg':seg}[id])},MutationObserver:class{observe(){}},Event:class{}});
 listener({target:{closest:()=>button}});assert.equal(select.value,wanted);assert.equal(applied,1);
}
''', TEMPLATES / "live.html")

    def test_closed_field_tooltips_do_not_contribute_scroll_width(self):
        css = (STATIC / "css/term-help.css").read_text(encoding="utf-8")
        self.assertIn("label .field-help.field-tip{display:none}", css)
        self.assertIn(".field-tip-anchor.open .field-tip{display:block", css)


if __name__ == "__main__":
    unittest.main()
