/* Print the HTML source as paginated, portable PDF. Requires Chrome; no npm deps. */
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {parseArgs}=require('node:util');
const {values}=parseArgs({options:{source:{type:'string',default:'docs/project-report.html'},
  output:{type:'string',default:'docs/project-report.pdf'},chrome:{type:'string',default:'/usr/bin/google-chrome'}}});
const source=path.resolve(values.source),output=path.resolve(values.output);
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'wildfire-report-pdf-'));
const chrome=spawn(values.chrome,['--headless=new','--disable-gpu','--disable-extensions',
  '--disable-background-networking','--no-first-run','--no-default-browser-check',
  '--remote-debugging-pipe','--user-data-dir='+profile],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let serial=0,buffer='';const pending=new Map();
chrome.stderr.on('data',()=>{});
chrome.on('error',error=>{for(const p of pending.values())p.reject(error)});
for(const pipe of [chrome.stdio[3],chrome.stdio[4]])pipe.on('error',error=>{for(const p of pending.values())p.reject(error)});
chrome.stdio[4].on('data',chunk=>{buffer+=chunk;let end;while((end=buffer.indexOf('\0'))>=0){
 const raw=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!raw)continue;const r=JSON.parse(raw);
 if(r.id&&pending.has(r.id)){const p=pending.get(r.id);pending.delete(r.id);clearTimeout(p.timer);
 r.error?p.reject(Error(JSON.stringify(r.error))):p.resolve(r.result||{})}
}});
function rpc(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++serial;
 const timer=setTimeout(()=>{pending.delete(id);reject(Error('Timed out: '+method))},30000);
 pending.set(id,{resolve,reject,timer});chrome.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0')})}
async function evaluate(sessionId,expression){const r=await rpc('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sessionId);
 if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||'Page script failed');return r.result.value}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

(async()=>{try{
 const {targetId}=await rpc('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await rpc('Target.attachToTarget',{targetId,flatten:true});await rpc('Page.enable',{},sessionId);
 await rpc('Emulation.setDeviceMetricsOverride',{width:794,height:1123,deviceScaleFactor:1,mobile:false},sessionId);
 await rpc('Page.navigate',{url:pathToFileURL(source).href},sessionId);
 for(let i=0;i<60;i++){if(await evaluate(sessionId,'document.readyState')==='complete')break;await delay(50)}
 await evaluate(sessionId,'document.fonts.ready.then(()=>true)');
 const preparation=await evaluate(sessionId,`(()=>{
  const main=document.querySelector('main.wrap');
  const introductory=document.querySelector('.intro');
  const overview=document.querySelector('.entry');
  const harness=document.querySelector('.harness-card');
  const firstPair=[...main.querySelectorAll(':scope > .two')][0];
  const reward=firstPair.children[0],data=firstPair.children[1];
  const synthetic=document.getElementById('synthetic-data');
  const evidence=document.getElementById('evidence-title').closest('section');
  const trace=document.getElementById('trace-title').closest('article');
  const learning=document.getElementById('learning-title').closest('article');
  const limits=document.getElementById('limits-title').closest('section');
  const generation=document.createElement('section');generation.className='card';
  for(const selector of ['.card-head','.generation-pipeline','.method-key']){
   const node=synthetic.querySelector(selector);if(!node)throw Error('Missing print section '+selector);generation.append(node);
  }
  const rules=document.createElement('section');rules.className='card';rules.append(synthetic.querySelector('.synthetic-rule-panel'));
  const oldHeading=rules.querySelector('h3'),rulesHeading=document.createElement('h2');
  rulesHeading.textContent=oldHeading.textContent;oldHeading.replaceWith(rulesHeading);
  const details=document.createElement('section');details.className='card';
  const heading=document.createElement('h2');heading.textContent='Synthetic inputs: estimates and variation';details.append(heading);
  details.append(synthetic.querySelector('.data-detail'),synthetic.querySelector('.generation-foot'));
  const groups=[
   [document.querySelector('.masthead'),introductory,overview], [harness,reward], [data],
   [generation], [rules], [details], [evidence], [trace,learning],
   [limits,document.querySelector('nav.footer'),document.querySelector('footer.bottom-line')]
  ];
  const pages=groups.map((nodes,i)=>{const p=document.createElement('div');p.className='pdf-page';p.dataset.page=i+1;
   for(const node of nodes){if(!node)throw Error('Missing page content');p.append(node)}return p});
  main.replaceChildren(...pages);document.querySelectorAll('details').forEach(d=>d.open=true);
  const walker=document.createTreeWalker(main,NodeFilter.SHOW_TEXT);let textNode;
  while(textNode=walker.nextNode())textNode.nodeValue=textNode.nodeValue.replace(/[\u2010-\u2015\u2212]/g,'-');
  document.title=document.title.replace(/[\u2010-\u2015\u2212]/g,'-');
  document.querySelectorAll('a[href]').forEach(a=>{const href=a.getAttribute('href');
   if(href.startsWith('file:'))throw Error('Local machine link in report');
   if(href&&!href.startsWith('#'))a.href=new URL(href,'https://github.com/jucamohedano/wildfire-response-env/blob/main/docs/project-report.html').href;
  });
  const style=document.createElement('style');style.textContent=\`
   @page{size:A4 portrait;margin:12mm 12mm 14mm}
   html,body{width:186mm;margin:0;padding:0;background:white;font-size:13px;line-height:1.45}
   main.wrap{width:186mm;max-width:none;padding:0;margin:0}
   .pdf-page{break-after:page;page-break-after:always;padding:0;margin:0;min-width:0}
   .pdf-page:last-child{break-after:auto}.pdf-page>.card{margin:0 0 10px}
   .pdf-page[data-page="2"]{zoom:.94;width:calc(186mm / .94)}
   .pdf-page[data-page="7"]{zoom:.92;width:calc(186mm / .92)}
   .card{padding:14px;border-radius:10px;break-inside:avoid}.card-head{margin-bottom:10px}
   h1{font-size:27px}h2{font-size:19px}h3{font-size:14px}.print{display:none!important}
   .masthead{margin-bottom:12px}.masthead-right a{font-size:9px;max-width:none;overflow-wrap:normal}.masthead-right p{font-size:10px}
   .flow{grid-template-columns:1fr 1fr;gap:9px}.flow-card{min-height:139px;padding:10px}.flow-card:after{display:none!important}
   .flow-card p{font-size:12px}.flow-card .bottom{font-size:10px;padding-top:7px}.intro h2{font-size:21px}
   .intro .lede{font-size:13px;margin-bottom:12px}.flow-card h3{font-size:14px}.step{font-size:10px!important}
   .entry h2{font-size:21px;line-height:1.3}.entry .description{font-size:13px}.entry .badges{margin:7px 0}
   .entry .callout{margin:10px 0;font-size:11px;padding:9px}.entry .foot{font-size:11px;padding-top:8px;margin-top:9px}
   .facts{padding-top:8px}.entry .meta{font-size:11px;margin:5px 0}.facts strong{font-size:12px}
   .harness-figure{max-width:540px;margin:auto}.harness-agent{padding:7px}.harness-label{font-size:11px}
   .harness-label small{font-size:10px;float:none;display:inline}.harness-exchange,.harness-wire{padding:5px 0}
   .harness-controls{margin-top:7px}.harness-controls span{padding:5px;font-size:10px}.harness-shell{padding:9px}
   .harness-world{padding:7px}.harness-output>div{padding:7px}.harness-figure>figcaption{font-size:10px}
   .harness-output small{font-size:10px}.private-wire{font-size:10px}.tools{margin-top:8px;gap:5px 12px}
   .tool-group{padding-top:5px}.tool-group code{font-size:10px}.clock-key{margin-top:6px;padding-top:6px}
   .reward-body{grid-template-columns:140px 1fr;gap:13px}.donut{width:130px;height:130px}
   .quiet-note{margin-top:9px;padding:8px 10px;font-size:11px}.reward-definition{font-size:12px}.checkline{font-size:11px;margin-top:8px}
   .reward-body+h3{margin:0}.source-row p{font-size:12px}.source-row strong{font-size:12px}
   .input-maps svg{max-height:220px}.input-maps{gap:15px}.input-maps figcaption,.source-caption{font-size:11px}
   .input-maps .map-title b{font-size:13px}.input-maps .map-title span{font-size:10px}.weather-strip{padding:9px}
   .weather-strip small{font-size:11px}.weather-fields span{font-size:10px}.format-strip{font-size:11px}
   .generation-pipeline{margin:7px 0 10px}.pipeline-inputs{grid-template-columns:repeat(3,1fr);gap:8px}
   .pipeline-inputs>div{padding:8px}.pipeline-inputs b{font-size:12px;margin:3px 0}.pipeline-inputs small{font-size:10px}
   .pipeline-inputs small br{display:initial}.pipeline-outputs{grid-template-columns:1.3fr 1fr}
   .pipeline-join{height:21px}.pipeline-down{height:18px;line-height:18px;font-size:17px}
   .pipeline-build,.pipeline-warm,.pipeline-gate{padding:7px 10px}.pipeline-build span,.pipeline-warm span,.pipeline-gate span{font-size:10px;margin-top:2px}
   .pipeline-build b,.pipeline-warm b,.pipeline-gate b{font-size:12px}.pipeline-episode{padding:8px 10px}
   .pipeline-loop{margin:5px auto;gap:8px}.pipeline-loop>div{padding:6px}.pipeline-loop small{font-size:10px}
   .pipeline-episode p{font-size:10px;margin:4px 0}.pipeline-outputs{margin-top:8px;gap:8px}.pipeline-outputs>div{padding:7px}
   .pipeline-count{font-size:10px;margin-top:7px}.method-key{grid-template-columns:repeat(4,1fr);margin-bottom:9px;gap:7px}.method-key>div{padding:6px 8px}
   .method-key b{font-size:11px}.method-key span{font-size:10px}.synthetic-rule-panel h3{font-size:14px;margin:9px 0 7px}
   .synthetic-rule-panel .rules-grid{grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:7px}
   .synthetic-rule-panel .rules-grid>div{padding:8px}.synthetic-rule-panel .rules-grid b{font-size:11px}
   .synthetic-rule-panel .rules-grid span{font-size:10px;line-height:1.4}.rule-sources{font-size:10px;margin:7px 0 0}
   .pdf-page[data-page="5"] .rules-grid{grid-template-columns:1fr 1fr;margin-top:16px;gap:12px}
   .pdf-page[data-page="5"] .rules-grid>div{padding:14px;break-inside:avoid}
   .pdf-page[data-page="5"] .rules-grid>div:last-child{grid-column:1/-1}
   .pdf-page[data-page="5"] .rules-grid b{font-size:14px}.pdf-page[data-page="5"] .rules-grid span{font-size:12px;line-height:1.5}
   .pdf-page[data-page="5"] .rule-sources{font-size:11px;margin-top:15px}
   .data-detail{grid-template-columns:1fr 1fr;gap:16px}.data-detail h3{font-size:14px}.gap-row{grid-template-columns:88px 1fr;gap:9px;padding:10px 0}
   .gap-row p{font-size:11px}.gap-label strong{font-size:11px}.gap-legend{grid-template-columns:repeat(3,1fr);font-size:10px}
   .gap-chart>figcaption{font-size:10px}.variation-grid{gap:7px}.variation-grid>div{padding:9px}.variation-grid span{font-size:11px}
   .variation-grid small{font-size:10px}.generation-foot{font-size:11px;margin-top:12px}.seed-note{font-size:11px}
   details.archive-parameters{font-size:11px}.archive-parameters p{font-size:11px}.badge{font-size:10px;padding:2px 7px}
   .runtime-columns{grid-template-columns:1fr 1fr;gap:13px}.runtime-review h2{font-size:19px}.runtime-review p{font-size:11px;margin:5px 0 8px}
   .runtime-metrics{margin:12px 0;gap:8px}.runtime-metrics>div{padding:8px}.runtime-metrics b{font-size:23px}
   .runtime-metrics small{font-size:10px}.runtime-grid{grid-template-columns:minmax(73px,1.2fr) repeat(4,minmax(38px,1fr));gap:3px;font-size:10px;margin:8px 0}
   .runtime-grid .head{font-size:9px}.runtime-grid .cell{padding:4px 2px}.runtime-review h3{font-size:14px}
   .runtime-durations{grid-template-columns:repeat(3,1fr);gap:7px;margin-top:9px}.runtime-durations>div{padding:8px}
   .runtime-durations b{font-size:16px}.runtime-durations small{font-size:10px}.runtime-footer{font-size:10px;margin-top:11px}
   .timeline li{font-size:12px;margin:9px 0}.timeline li p{font-size:11px}.timeline li b{font-size:12px}
   .learning{grid-template-columns:repeat(4,1fr);gap:9px}.learn-step p{font-size:11px}.storage-lines{margin-top:10px;gap:7px}
   .storage-line{font-size:11px;grid-template-columns:90px 1fr;gap:9px}.example-summary{font-size:11px;margin-top:10px;padding-top:8px}
   .phase-legend{font-size:10px}.phase-chart{margin:10px 0}.phase-chart>figcaption{font-size:11px}
   .calibration-layout{grid-template-columns:1fr 1fr;gap:15px}.remaining-panel p{font-size:11px}
   .perimeter-lane{font-size:10px}.calibration-loop{gap:6px}.calibration-loop small{font-size:10px}
   .details{display:block!important;font-size:11px}.details code{font-size:10px}.mono,code{overflow-wrap:anywhere}
   .robustness-next{margin-top:12px;padding-top:10px}.robustness-next span{font-size:10px}
   .footer{margin-top:12px;gap:7px;grid-template-columns:repeat(4,1fr)}.footer a{padding:9px}
   .footer span,.footer code{font-size:10px}.bottom-line{font-size:10px;margin-top:10px}
   a{color:#6550b9!important}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  \`;document.head.append(style);
  return {pages:pages.length,links:document.querySelectorAll('a').length};
 })()`);
 await rpc('Emulation.setEmulatedMedia',{media:'print'},sessionId);
 const sizes=await evaluate(sessionId,`[...document.querySelectorAll('.pdf-page')].map(p=>({page:+p.dataset.page,height:p.getBoundingClientRect().height,overflow:[...p.querySelectorAll('*')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>document.body.getBoundingClientRect().right+2||r.left<document.body.getBoundingClientRect().left-2)}).map(e=>e.tagName+'.'+e.className).slice(0,4)}))`);
 if(sizes.some(p=>p.height>1020||p.overflow.length))throw Error('Print page overflow: '+JSON.stringify(sizes));
 fs.mkdirSync(path.dirname(output),{recursive:true});
 const result=await rpc('Page.printToPDF',{printBackground:true,preferCSSPageSize:true,generateTaggedPDF:true,
  displayHeaderFooter:true,headerTemplate:'<div></div>',footerTemplate:'<div style="font:8px Arial;color:#888;width:100%;text-align:center">Wildfire Ops · Research prototype &nbsp; | &nbsp; <span class="pageNumber"></span> / <span class="totalPages"></span></div>'},sessionId);
 const bytes=Buffer.from(result.data,'base64');
 const printedPages=(bytes.toString('latin1').match(/\/Type\s*\/Page\b/g)||[]).length;
 if(printedPages!==preparation.pages)throw Error('Unexpected PDF pagination: '+printedPages+' instead of '+preparation.pages);
 fs.writeFileSync(output,bytes);
 process.stdout.write(JSON.stringify({output,...preparation,pageLayout:sizes,bytes:fs.statSync(output).size})+'\n');
 }finally{try{await rpc('Browser.close')}catch{}chrome.kill('SIGTERM');for(const p of pending.values())clearTimeout(p.timer);fs.rmSync(profile,{recursive:true,force:true});}
})().catch(error=>{process.stderr.write(error.message+'\n');process.exitCode=1});
