(() => {
  "use strict";
  const tabs=[...document.querySelectorAll('.registration-tabs [role="tab"]')];if(!tabs.length)return;
  function select(tab){tabs.forEach(item=>{const active=item===tab;item.setAttribute("aria-selected",String(active));item.tabIndex=active ? 0 : -1;document.getElementById(item.getAttribute("aria-controls")).hidden=!active;});if(tab.id!=="register-tab-pets"){document.getElementById("scene-registry-panel").open=true;document.dispatchEvent(new CustomEvent("meowkeeper:scene-load"));}}
  tabs.forEach((tab,index)=>{tab.addEventListener("click",()=>select(tab));tab.addEventListener("keydown",event=>{let next;if(event.key==="ArrowRight")next=(index+1)%tabs.length;if(event.key==="ArrowLeft")next=(index+tabs.length-1)%tabs.length;if(event.key==="Home")next=0;if(event.key==="End")next=tabs.length-1;if(next!==undefined){event.preventDefault();select(tabs[next]);tabs[next].focus();}});});select(tabs[0]);
})();
