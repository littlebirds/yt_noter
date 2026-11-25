const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function frontend() {
  const html = fs.readFileSync('static/index.html', 'utf8');
  const elements = new Map();
  for (const match of html.matchAll(/id="([^"]+)"/g)) {
    elements.set('#' + match[1], {
      value: '', innerHTML: '', textContent: '', hidden: false, disabled: false, dataset: {},
      handlers: {}, classList: {toggle() {}},
      addEventListener(name, fn) {this.handlers[name] = fn;},
      scrollIntoView() {}, reset() {}, setAttribute(name,value) {this[name]=value;},
    });
  }
  elements.get('#selection-mode').value = 'manual';
  elements.get('#selection-count').value = '5';
  elements.get('#scan-limit').value = '50';
  const calls = [];
  const storage = () => ({getItem() {return null;},setItem() {},removeItem() {}});
  const context = vm.createContext({
    document: {
      querySelector(selector) {if (!elements.has(selector)) throw new Error('Missing element: ' + selector);return elements.get(selector);},
      querySelectorAll() {return [];}, documentElement: {},
    },
    window: {scrollTo() {}}, localStorage: storage(), sessionStorage: storage(),
    setTimeout() {}, clearTimeout() {}, setInterval() {}, URL, URLSearchParams, console,
    fetch: async (path, options) => {
      calls.push({path, options});
      const data = path.startsWith('/api/library') ? {videos:[],creators:[],stats:{videos:0,transcripts:0,summaries:0,tickers:0}} :
        path === '/api/jobs' ? {jobs:[]} : path === '/api/discover' ? {job_id:'test-job'} : {};
      return {ok:true,json:async()=>data};
    },
  });
  vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return {context,elements,calls,run:code=>vm.runInContext(code,context)};
}

test('latest N selects newest preview entries without selecting the rest',async()=>{
  const ui=await frontend();
  ui.run("discovery=[{id:'one'},{id:'two'},{id:'three'}]");
  ui.elements.get('#selection-mode').value='latest';
  ui.elements.get('#selection-count').value='2';
  ui.run('applySelection()');
  assert.deepEqual(Array.from(ui.run('[...selected]')),['one','two']);
});

test('Last N days selects only dated videos within the window',async()=>{
 const ui=await frontend();
 ui.run("discovery=[{id:'recent',published_at:'2026-01-09T12:00:00Z'},{id:'old',published_at:'2025-12-01T00:00:00Z'},{id:'future',published_at:'2027-01-01T00:00:00Z'},{id:'unknown',published_at:null}];discoverySince='2026-01-10T12:00:00Z'");
 ui.elements.get('#selection-mode').value='window';ui.elements.get('#selection-count').value='5';
 ui.run('applySelection()');
 assert.deepEqual(Array.from(ui.run('[...selected]')),['recent']);
 assert.equal(ui.elements.get('#selection-count').disabled,false);
});

test('feed discovery does not use the selection duration as a subscription boundary',async()=>{
  const ui=await frontend();
  ui.elements.get('#source-url').value='https://www.youtube.com/@YHGJ9527/posts';
  ui.elements.get('#selection-count').value='6';
  await ui.elements.get('#discover-form').handlers.submit({preventDefault(){}});
  const call=ui.calls.find(c=>c.path==='/api/discover');
  const body=JSON.parse(call.options.body);
  assert.equal(body.url,'https://www.youtube.com/@YHGJ9527/posts');
  assert.equal(body.since,undefined);
  assert.equal(body.limit,50);
});

test('Markdown escapes untrusted HTML and does not link script URLs',async()=>{
  const ui=await frontend();
  const rendered=ui.run("markdown('# Heading\\n<script>alert(1)</script>\\n[bad](javascript:alert(1))\\n**重点**')");
  assert.ok(rendered.includes('&lt;script&gt;'));
  assert.ok(!rendered.includes('<script>'));
  assert.ok(!rendered.includes('href="javascript:'));
  assert.ok(rendered.includes('<strong>重点</strong>'));
});

test('Chinese UI has translated feed controls and does not alter research text',async()=>{
  const ui=await frontend();
  ui.run("lang='zh'");
  assert.equal(ui.run("t('dateWindow')"),'最近 N 天');
  assert.equal(ui.run("t('sync')"),'检查频道并补齐历史');
  assert.equal(ui.run("esc('原文 NVDA')"),'原文 NVDA');
});


test('appending discovery pages preserves selection and deduplicates overlap',async()=>{
  const ui=await frontend();
  ui.run("acceptDiscoveryPage({source:'channel',offset:0,limit:2,scanned:2,possibly_more:true,next_offset:2,videos:[{id:'one'},{id:'two'}],warnings:['first warning']});selected.add('one')");
  ui.run("acceptDiscoveryPage({source:'channel',offset:2,limit:2,scanned:2,possibly_more:true,next_offset:4,videos:[{id:'two'},{id:'three'}],warnings:[]})");
  assert.deepEqual(Array.from(ui.run('discovery.map(v=>v.id)')),['one','two','three']);
  assert.deepEqual(Array.from(ui.run('[...selected]')),['one']);
  assert.equal(ui.run('discoveryResult.scanned_total'),4);
  assert.equal(ui.run('discoveryResult.warnings.length'),1);
});

test('load more submits the scan cursor rather than the successful video count',async()=>{
  const ui=await frontend();
  ui.run("discoveryResult={source:'https://www.youtube.com/@test/videos',limit:50,scanned:50,next_offset:50,possibly_more:true};discovery=Array(23).fill({id:'one'})");
  await ui.elements.get('#load-more').handlers.click();
  const call=ui.calls.find(c=>c.path==='/api/discover');
  assert.equal(JSON.parse(call.options.body).offset,50);
  assert.equal(JSON.parse(call.options.body).limit,50);
});


test('manual disables the shared number field and preserves checkbox selection',async()=>{
 const ui=await frontend();
 ui.run("selected=new Set(['chosen'])");
 for(const mode of ['latest','window','manual']) {
  ui.elements.get('#selection-mode').value=mode;ui.run('selectionControls()');
  assert.equal(ui.elements.get('#selection-count').disabled,mode==='manual');
 }
 ui.elements.get('#selection-count').value='';ui.run('applySelection()');
 assert.deepEqual(Array.from(ui.run('[...selected]')),['chosen']);
});

test('automatic selection rejects invalid N and clears stale selections',async()=>{
 const ui=await frontend();ui.elements.get('#selection-mode').value='latest';
 for(const value of ['', '0','-1','1.5','bad']) {
  ui.run("selected=new Set(['old'])");ui.elements.get('#selection-count').value=value;
  assert.throws(()=>ui.run('applySelection()'));
  assert.equal(ui.run('selected.size'),0);
 }
});

test('one shared numeric input sits immediately after the selection dropdown',()=>{
 const html=fs.readFileSync('static/index.html','utf8');
 assert.ok(html.indexOf('id="selection-count"')>html.indexOf('id="selection-mode"'));
 assert.ok(!html.includes('id="go-back-unit"') && !html.includes('id="latest-n"'));
 assert.match(html,/id="selection-count"[^>]*disabled/);
});

test('lookup progress remains visible below the source input',async()=>{
 const ui=await frontend();
 ui.run("activeDiscovery='lookup'");
 ui.context.fetch=async()=>({ok:true,json:async()=>({jobs:[{id:'lookup',kind:'discover',status:'running',progress:'Reading publication dates 3/50',created_at:new Date().toISOString()}]})});
 await ui.run('poll()');
 assert.equal(ui.elements.get('#discovery-status').hidden,false);
 assert.match(ui.elements.get('#discovery-status').textContent,/3\/50/);
});

test('failed lookup keeps error visible and enables retry',async()=>{
 const ui=await frontend();
 ui.run("activeDiscovery='lookup'");
 ui.elements.get('#discover-button').disabled=true;
 ui.context.fetch=async()=>({ok:true,json:async()=>({jobs:[{id:'lookup',kind:'discover',status:'failed',error:'YouTube requires authentication',created_at:new Date().toISOString()}]})});
 await ui.run('poll()');
 assert.match(ui.elements.get('#discovery-status').textContent,/requires authentication/);
 assert.equal(ui.elements.get('#discover-button').disabled,false);
 assert.equal(ui.run('activeDiscovery'),null);
});


test('Enter submits the source form once and respects IME composition',async()=>{
 const ui=await frontend();let submits=0,prevented=0;
 ui.elements.get('#discover-form').requestSubmit=()=>{submits++;};
 const enter=ui.elements.get('#source-url').handlers.keydown;
 enter({key:'Enter',isComposing:false,preventDefault(){prevented++;}});
 assert.equal(submits,1);assert.equal(prevented,1);
 enter({key:'Enter',isComposing:true,preventDefault(){throw Error('IME interrupted');}});
 ui.elements.get('#discover-button').disabled=true;
 enter({key:'Enter',isComposing:false,preventDefault(){}});
 assert.equal(submits,1);
});

test('Save requires selection but is independent of summaries',async()=>{
 const ui=await frontend();ui.run('updateSelected()');
 assert.equal(ui.elements.get('#process-selected').disabled,true);
 assert.equal(ui.elements.get('#save-selected').disabled,true);
 ui.run("discovery=[{id:'one',summary_ready:true},{id:'two',summary_ready:false}];selected=new Set(['one','two']);updateSelected()");
 assert.equal(ui.elements.get('#process-selected').disabled,false);
 assert.equal(ui.elements.get('#save-selected').disabled,false);
 ui.run("jobs=[{kind:'process',video_id:'two',status:'done'}];updateSelected()");
 assert.equal(ui.elements.get('#save-selected').disabled,false);
});

test('Add to Library sends only the selected ids and discovered source',async()=>{
 const ui=await frontend();
 ui.run("selected=new Set(['one']);discoveryResult={source:'https://www.youtube.com/@research/videos'};discovery=[{id:'one',summary_ready:true},{id:'two',summary_ready:true}]");
 await ui.elements.get('#save-selected').handlers.click();
 const call=ui.calls.find(c=>c.path==='/api/library' && c.options.method==='POST');
 assert.deepEqual(JSON.parse(call.options.body),{ids:['one'],url:'https://www.youtube.com/@research/videos'});
});

test('research actions are above selection and the list',()=>{
 const html=fs.readFileSync('static/index.html','utf8');
 assert.ok(html.indexOf('id="process-selected"')<html.indexOf('id="selection-mode"'));
 assert.ok(html.indexOf('id="save-selected"')<html.indexOf('id="discovery-list"'));
});

test('library groups feed videos and standalone videos with separate actions',async()=>{
 const ui=await frontend();
 ui.run(`libraryData={creators:[],stats:{videos:2,transcripts:2,summaries:2,tickers:0},videos:[
 {id:'one',title:'Feed video',creator:'Creator',summary_preview:'Summary',category:'finance'},
 {id:'two',title:'Standalone',creator:'Creator',summary_preview:'Summary',category:'other'}
 ],groups:[{id:'feed1',title:'Creator <safe>',url:'https://www.youtube.com/@creator/videos',enabled:1,video_ids:['one']}]};renderLibrary()`);
 const html=ui.elements.get('#library-list').innerHTML;
 assert.match(html,/Creator &lt;safe&gt;/);
 assert.match(html,/Ungrouped/);
 assert.match(html,/data-stop-feed="feed1"/);
 assert.equal((html.match(/data-video="one"/g)||[]).length,1);
 assert.match(html,/data-delete-summary="two"/);
 ui.run('libraryData.groups[0].enabled=0;renderLibrary()');
 assert.doesNotMatch(ui.elements.get('#library-list').innerHTML,/data-stop-feed/);
 assert.match(ui.elements.get('#library-list').innerHTML,/Not watching/);
});

test('queue shows running first, positions, titles, stages and persistent overview',async()=>{
 const ui=await frontend();
 ui.run(`jobs=[
 {id:'done',kind:'process',status:'done',title:'Finished video',video_id:'done',created_at:'2026-01-01'},
 {id:'wait',kind:'process',status:'queued',title:'Waiting video',video_id:'wait',queue_position:2,lane:'processing',created_at:'2026-01-01'},
 {id:'run',kind:'process',status:'running',title:'Current <video>',video_id:'run',progress:'Summarizing section 2/8 in zh',created_at:'2026-01-01',updated_at:'2026-01-02'}];renderJobs()`);
 const html=ui.elements.get('#job-list').innerHTML;
 assert.ok(html.indexOf('Current &lt;video&gt;')<html.indexOf('Waiting video'));
 assert.ok(html.indexOf('Waiting video')<html.indexOf('Finished video'));
 assert.match(html,/Position 2/);
 assert.match(html,/Summarizing section 2\/8/);
 assert.match(html,/Since last update/);
 assert.equal(ui.elements.get('#queue-count').textContent,2);
 assert.equal(ui.elements.get('#queue-overview').hidden,false);
 assert.match(ui.elements.get('#queue-overview').textContent,/Current <video>/);
});

test('queue refresh failure visibly marks stale data',async()=>{
 const ui=await frontend();
 ui.run("jobs=[{id:'one',kind:'process',status:'queued',created_at:'2026-01-01'}];renderJobs()");
 ui.context.fetch=async()=>{throw Error('offline');};
 await assert.rejects(ui.run('poll()'),/offline/);
 assert.match(ui.elements.get('#queue-status').textContent,/out of date/);
 assert.match(ui.elements.get('#queue-overview').textContent,/out of date/);
});

test('Summarize skips completed and active videos and blocks rapid repeated clicks',async()=>{
 const ui=await frontend();
 ui.run("discovery=[{id:'saved',summary_ready:true},{id:'active'},{id:'new'}];selected=new Set(['saved','active','new']);jobs=[{id:'existing',kind:'process',video_id:'active',status:'running'}];updateSelected()");
 let resolveRequest, submissions=0;
 ui.context.fetch=async(path,options)=>{
  if(path==='/api/process') {
   submissions++;
   assert.deepEqual(JSON.parse(options.body).ids,['new']);
   return new Promise(resolve=>{resolveRequest=()=>resolve({ok:true,json:async()=>({job_ids:['accepted']})});});
  }
  throw Error('status temporarily unavailable');
 };
 const click=ui.elements.get('#process-selected').handlers.click;
 const first=click();
 assert.equal(ui.elements.get('#process-selected').disabled,true);
 ui.run('updateSelected()');
 assert.equal(ui.elements.get('#process-selected').disabled,true);
 await click();
 assert.equal(submissions,1);
 resolveRequest();await first;
 assert.equal(ui.elements.get('#process-selected').disabled,true);
 await click();assert.equal(submissions,1);
});

test('Summarize enables retry after a failed job but not for an existing summary',async()=>{
 const ui=await frontend();
 ui.run("selected=new Set(['one']);discovery=[{id:'one'}];jobs=[{video_id:'one',kind:'process',status:'failed'}];updateSelected()");
 assert.equal(ui.elements.get('#process-selected').disabled,false);
 ui.run("discovery[0].summary_ready=true;updateSelected()");
 assert.equal(ui.elements.get('#process-selected').disabled,true);
});

test('Add to Library allows repeated idempotent saves and blocks concurrent clicks',async()=>{
 const ui=await frontend();
 ui.run("discoveryResult={source:'https://www.youtube.com/@research/videos'};selected=new Set(['old','new']);discovery=[{id:'old',summary_ready:true,saved_sources:[discoveryResult.source]},{id:'new',summary_ready:true}];updateSelected()");
 let resolveRequest, submissions=0;
 ui.context.fetch=async(path,options)=>{
  if(path==='/api/library' && options.method==='POST') {
   submissions++;assert.deepEqual(JSON.parse(options.body).ids,['old','new']);
   return new Promise(resolve=>{resolveRequest=()=>resolve({ok:true,json:async()=>({saved:1})});});
  }
  throw Error('refresh failed');
 };
 const click=ui.elements.get('#save-selected').handlers.click, first=click();
 ui.run('updateSelected()');assert.equal(ui.elements.get('#save-selected').disabled,true);
 await click();assert.equal(submissions,1);
 resolveRequest();await first;
 assert.equal(ui.elements.get('#save-selected').disabled,false);
 ui.run("discoveryResult.source='https://www.youtube.com/@another/videos';updateSelected()");
 assert.equal(ui.elements.get('#save-selected').disabled,false);
});

test('Add to Library submits unsummarized videos and remains enabled afterward',async()=>{
 const ui=await frontend();
 ui.run("selected=new Set(['one']);discoveryResult={source:'https://www.youtube.com/@research/videos'};discovery=[{id:'one',summary_ready:false}];jobs=[{video_id:'one',kind:'process',status:'running'}];updateSelected()");
 assert.equal(ui.elements.get('#save-selected').disabled,false);
 await ui.elements.get('#save-selected').handlers.click();
 const call=ui.calls.find(c=>c.path==='/api/library' && c.options.method==='POST');
 assert.deepEqual(JSON.parse(call.options.body),{ids:['one'],url:'https://www.youtube.com/@research/videos'});
 assert.equal(ui.elements.get('#save-selected').disabled,false);
});

test('LLM dialog loads redacted settings and saves configuration',async()=>{
 const ui=await frontend();let opened=false,closed=false;
 ui.elements.get('#llm-dialog').showModal=()=>{opened=true;};
 ui.elements.get('#llm-dialog').close=()=>{closed=true;};
 ui.context.fetch=async(path,options)=>{
  ui.calls.push({path,options});
  return {ok:true,json:async()=>({endpoint:'http://localhost:8080/v1',model:'small',format:'local',key_configured:true})};
 };
 await ui.elements.get('#llm-config').handlers.click();
 assert.equal(opened,true);assert.equal(ui.elements.get('#llm-key').value,'');
 assert.match(ui.elements.get('#llm-key-status').textContent,/saved/);
 ui.elements.get('#llm-endpoint').value='https://example.com/v1';
 ui.elements.get('#llm-model').value='remote';ui.elements.get('#llm-format').value='json';
 ui.elements.get('#llm-key').value='secret';
 await ui.elements.get('#llm-form').handlers.submit({preventDefault(){}});
 const call=ui.calls.find(c=>c.path==='/api/llm-settings'&&c.options.method==='POST');
 assert.equal(JSON.parse(call.options.body).model,'remote');
 assert.equal(closed,true);assert.equal(ui.elements.get('#llm-key').value,'');
});

test('Slides offers four-second scanning and distinguishes empty scans',async()=>{
 const ui=await frontend();
 const form={addEventListener(name,fn){this[name]=fn;}};
 ui.elements.set('#slide-form',form);
 ui.run("video={id:'abcdefghijk',slides:[],slide_scan:null}; renderSlides(document.querySelector('#detail-content'))");
 assert.match(ui.elements.get('#detail-content').innerHTML,/value="4"/);
 assert.match(ui.elements.get('#detail-content').innerHTML,/Scan this video to find slides/);
 ui.run("video.slide_scan={minimum:6};renderSlides(document.querySelector('#detail-content'))");
 assert.match(ui.elements.get('#detail-content').innerHTML,/No stable slide candidates/);
 assert.match(ui.elements.get('#detail-content').innerHTML,/value="6"/);
});

test('Slide scrubber requests selected image and links original timestamp',async()=>{
 const ui=await frontend();
 for(const id of ['slide-position','slide-original','slide-image','slide-message','slide-first','slide-previous','slide-next','slide-last'])ui.elements.set('#'+id,{});
 ui.run("activeTab='slides';video={id:'abcdefghijk',url:'https://www.youtube.com/watch?v=abcdefghijk',slides:[{position:0,start:12,end:20,timestamp:12}]}");
 await ui.run('showSlide(0)');
 assert.equal(ui.elements.get('#slide-original').href,'https://www.youtube.com/watch?v=abcdefghijk&t=12s');
 assert.match(ui.elements.get('#slide-position').textContent,/1 \/ 1/);
 assert.equal(ui.calls.at(-1).path,'/api/slides/abcdefghijk/0');
});

test('New app session clears stale draft results and makes the video eligible again',async()=>{
 const ui=await frontend();
 ui.run("acceptServerSession('session-one');discovery=[{id:'abcdefghijk',summary_ready:true}];selected.add('abcdefghijk');jobs=[{id:'old',video_id:'abcdefghijk',kind:'process',status:'done'}];video={id:'abcdefghijk',saved:false};activeDiscovery='old-discovery'");
 assert.equal(ui.run("canSummarize('abcdefghijk')"),false);
 ui.run("acceptServerSession('session-two')");
 assert.equal(ui.run('discovery.length'),0);
 assert.equal(ui.run('jobs.length'),0);
 assert.equal(ui.run('selected.size'),0);
 assert.equal(ui.run('video'),null);
 assert.equal(ui.run('activeDiscovery'),null);
 assert.equal(ui.run("canSummarize('abcdefghijk')"),true);
 assert.equal(ui.elements.get('#discovery').hidden,true);
});

test('Refreshing within the same app session keeps draft results',async()=>{
 const ui=await frontend();
 ui.run("acceptServerSession('same');discovery=[{id:'abcdefghijk',summary_ready:true}];acceptServerSession('same')");
 assert.equal(ui.run('discovery.length'),1);
});


test('Slide buttons navigate first, previous, next and last with boundary disabling',async()=>{
 const ui=await frontend();
 for(const id of ['slide-position','slide-original','slide-image','slide-message','slide-first','slide-previous','slide-next','slide-last','slide-form'])ui.elements.set('#'+id,{handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});
 ui.run("activeTab='slides';video={id:'abcdefghijk',url:'https://www.youtube.com/watch?v=abcdefghijk',slides:[0,1,2].map(i=>({position:i,start:i*10,end:i*10+4,timestamp:i*10}))};renderSlides(document.querySelector('#detail-content'))");
 assert.doesNotMatch(ui.elements.get('#detail-content').innerHTML,/type="range"|slide-scrubber/);
 assert.equal(ui.elements.get('#slide-first').disabled,true);
 assert.equal(ui.elements.get('#slide-previous').disabled,true);
 await ui.elements.get('#slide-next').handlers.click();assert.equal(ui.run('slideIndex'),1);
 await ui.elements.get('#slide-last').handlers.click();assert.equal(ui.run('slideIndex'),2);
 assert.equal(ui.elements.get('#slide-next').disabled,true);assert.equal(ui.elements.get('#slide-last').disabled,true);
 await ui.elements.get('#slide-previous').handlers.click();assert.equal(ui.run('slideIndex'),1);
 await ui.elements.get('#slide-first').handlers.click();assert.equal(ui.run('slideIndex'),0);
 ui.run('video.slides=video.slides.slice(0,1)');await ui.run('showSlide(99)');
 for(const id of ['first','previous','next','last'])assert.equal(ui.elements.get('#slide-'+id).disabled,true);
});
