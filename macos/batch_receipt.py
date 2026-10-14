"""Native receipt fields from the supplied pending-payment DOM, batch only."""
NATIVE_RECEIPTS_JS = r"""() => {
 const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden'};
 const found=new Map();
 for(const status of document.querySelectorAll('.FhGvsUYg')) {
  const own=[...status.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
  if(!visible(status)||!/^订单已提交[，,、\s]*待付款$/.test(own))continue;
  for(let box=status.parentElement;box&&box!==document.body;box=box.parentElement){
   const text=box.innerText||'';
   const id=text.match(/订单(?:号|编号)\s*[:：]?\s*([A-Za-z0-9_-]{6,})/);
   if(!id)continue;
   const cards=[...box.querySelectorAll('.iE4K8Yuq')].filter(visible);
   const names=cards.flatMap(c=>[...c.querySelectorAll('.mrZAfBZJ')].filter(visible).map(el=>el.innerText.trim()));
   const specs=cards.flatMap(c=>[...c.querySelectorAll('.b8ymizOj')].filter(visible).map(el=>el.innerText.trim()));
   const quantities=[...text.matchAll(/(?:购买数量|商品数量)\s*[:：]?\s*(\d+)/g)].map(x=>Number(x[1]));
   found.set(id[1],{id:id[1],status:own,names,specs,quantities,item_count:cards.length,native:true});break;
  }
 }
 for(const label of document.querySelectorAll('h1,h2,h3,p,span,div')){
  if(label.children.length||!visible(label)||!/^\s*(订单已提交[，,、\s]*待付款|待付款|订单待支付|等待付款|待支付)\s*$/.test(label.innerText||''))continue;
  for(let box=label.parentElement;box&&box!==document.body;box=box.parentElement){
   const text=box.innerText||'';
   const id=text.match(/订单(?:号|编号)\s*[:：]?\s*([A-Za-z0-9_-]{6,})/);
   if(id){if(!found.has(id[1]))found.set(id[1],{id:id[1],status:label.innerText.trim(),text,native:false});break;}
  }
 }
 return [...found.values()];
}"""
