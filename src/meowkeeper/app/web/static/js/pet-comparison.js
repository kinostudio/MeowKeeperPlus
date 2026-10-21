(() => {
  const form=document.getElementById('pet-compare-form'); if(!form)return;
  const message=document.getElementById('pet-compare-message'), results=document.getElementById('pet-compare-results');
  const preview=document.getElementById('pet-compare-preview');let previewUrl;
  form.addEventListener('submit',async event=>{
    event.preventDefault();const file=document.getElementById('pet-compare-image').files[0];if(!file)return;
    if(file.size>2*1024*1024){message.textContent='請選擇不超過 2 MB 的 JPG。';return;}
    if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=URL.createObjectURL(file);preview.src=previewUrl;preview.hidden=false;
    const button=form.querySelector('button');button.disabled=true;results.replaceChildren();message.textContent='AI 正在比對身分，首次載入模型可能較久…';
    const data=new FormData();data.append('image',file);
    const csrf=document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('meowkeeper_csrf='));
    try{
      const response=await fetch('/api/v1/pets/compare-photo',{method:'POST',headers:{'X-MeowKeeper-CSRF':csrf?decodeURIComponent(csrf.slice(16)):''},body:data});
      const item=await response.json();if(!response.ok)throw new Error(item.detail||'比對未完成。');
      message.textContent=`AI 身分候選：${item.predicted_name||'尚未確認'}｜融合相似度 ${item.similarity.toFixed(3)}｜候選差距 ${item.margin.toFixed(3)}。${item.message_zh_tw}`;
      item.candidates.forEach(candidate=>{
        const card=document.createElement('article');card.className='card';const title=document.createElement('h4');title.textContent=candidate.name;card.append(title);
        const score=document.createElement('p');score.textContent=`最接近參考照的視覺相似度：${candidate.reference_similarity.toFixed(3)}`;card.append(score);
        if(candidate.reference_url){const image=document.createElement('img');image.src=candidate.reference_url;image.alt=`${candidate.name}的近似參考照片`;image.style.width='100%';image.style.maxHeight='200px';image.style.objectFit='contain';card.append(image);}results.append(card);
      });
      if(!item.candidates.length)message.textContent+=' 尚無同模型版本的參考特徵，請先建立特徵。';
    }catch(error){message.textContent=error.message;}finally{button.disabled=false;}
  });
})();
