'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let lang = localStorage.getItem('frame-language') || 'en';
let currentView = 'discover', discovery = [], selected = new Set(), activeDiscovery = sessionStorage.getItem('frame-discovery');
let jobs = [], video = null, activeTab = 'summary', libraryData = null, statusData = null, polling = false;
let discoveryResult = null, feedsData = null, discoverySince = null;
const zh = {
 workspace:'我的工作区',newResearch:'新建研究',library:'资料库',queue:'处理队列',localFirst:'你的研究，你的服务器。',localSub:'本地模型，长期积累。',settings:'设置与配置',nativeBadge:'保留视频原语言',eyebrow:'少一点观看，多一点理解。',hero:'让视频变成<br>留下来的知识。',intro:'跟随有价值的观点，告别无尽的标签页。使用视频的原语言转录、总结，构建可检索的研究资料库。',addSource:'从一个来源开始',youtube:'YOUTUBE',sourceLabel:'视频、播放列表或频道链接',findVideos:'查找视频',postsHint:'频道 /posts 链接会解析为该频道的视频列表。',scanLimit:'最多扫描',videos:'个视频',featureLanguage:'保留原本的声音',featureLanguageText:'中文保持中文，英文保持英文。不采用自动翻译的字幕。',featureBatch:'一个视频，或整个频道',featureBatchText:'手动多选、选取最新 N 个，或按时间范围回溯。',featureSearch:'把线索连接起来',featureSearchText:'按股票代码、作者、主题和日期检索，每条观点都有出处。',sourceResults:'来源结果',selection:'选择方式',manual:'手动选择',latest:'最新 N 个视频',dateWindow:'时间范围',latestN:'视频数量',from:'起始日期',through:'截止日期（默认为现在）',to:'截止日期',selectAll:'全选',clear:'清空',summarizeSelected:'总结所选视频 →',researchArchive:'你的研究档案',libraryTitle:'多一点看问题的角度。',libraryIntro:'原文、观点与来源。在需要的时候，随时找到。',refresh:'刷新',search:'搜索',creator:'作者',topic:'主题',allCreators:'全部作者',allTopics:'全部主题',finance:'金融',geopolitics:'地缘政治',hardware:'硬件',technology:'科技',education:'教育',other:'其他',reset:'重置筛选',inProgress:'正在处理',queueTitle:'让有价值的观点慢慢汇集。',queueIntro:'串行处理，降低服务器负载。离开此页面也不影响任务，队列已持久保存。',underHood:'运行配置',settingsTitle:'简单，也可靠。',settingsIntro:'本地推理、持久存储，以及适合 CPU 的处理队列。',serverStatus:'服务状态',checkAgain:'重新检查',accessToken:'服务器访问令牌',tokenHint:'如果服务器配置了 AUTH_TOKEN，请在这里输入。令牌仅保存在当前浏览器标签页。',save:'保存',backLibrary:'← 返回资料库',watchOriginal:'观看原视频 ↗',regenerate:'生成 / 重试总结',export:'导出 Markdown ↓',summary:'总结',transcript:'原文',stockMentions:'股票提及',footer:'让研究更从容。',footerRight:'始终以来源为本。',emptyLibrary:'知识从第一个视频开始。',emptyLibrarySub:'添加视频或频道，即可将原文和总结保存到这里。',emptySearch:'没有匹配的记录',emptySearchSub:'尝试调整关键词或筛选条件。',emptyQueue:'队列还很安静。',emptyQueueSub:'添加来源，开启你的第一项研究。',noVideos:'没有发现可用的视频。',noSummary:'尚未生成总结。',noTranscript:'尚未保存原文。',noMentions:'未提取到有原文支持的股票代码。',mentionNote:'由模型提取，未经人工核实。提及不等于推荐；这里只保留原文明示的代码，不推测公司代码，也不计算收益。',cancelled:'已取消',queued:'等待中',running:'处理中',done:'已完成',failed:'失败',retry:'重试',process:'转录与总结',discover:'发现视频',unknownDate:'日期未知',unknownCreator:'未知作者',savedTranscript:'原文已保存',summarized:'已总结',pending:'待处理',selected:'已选择',scanBound:'当前结果仅包含本次扫描。较早的视频可能不在范围内。',missingDates:'日期未知的视频不会被纳入时间筛选。',addedQueue:'已加入处理队列。',savedToken:'访问令牌已保存到当前标签页。',ready:'就绪',unavailable:'未就绪',cpu:'CPU',model:'总结模型',speech:'语音转录',media:'视频读取',loading:'加载中…',scanning:'正在扫描；频道日期解析可能需要几分钟。',viewLibrary:'查看资料库 →',transcripts:'份原文',summaries:'份总结',tickers:'个股票代码',buy:'买入',sell:'卖出',hold:'持有',mention:'提及',source:'来源',language:'语言',noMore:'最多显示 1,000 条结果；请使用日期或作者缩小范围。',invalidWindow:'起始日期不能晚于截止日期。'
};
const en = {};
$$('[data-i18n]').forEach(el => { en[el.dataset.i18n] = el.innerHTML; });
Object.assign(en, {emptyLibrary:'Your library starts with one video.',emptyLibrarySub:'Add a video or channel to save transcripts and insights here.',emptySearch:'No matching research.',emptySearchSub:'Try another keyword or adjust your filters.',emptyQueue:'A little quiet in here.',emptyQueueSub:'Add a source to start your first research session.',noVideos:'No available videos found.',noSummary:'No summary yet.',noTranscript:'No saved transcript yet.',noMentions:'No source-backed stock symbols extracted.',mentionNote:'Model-extracted, not human-verified. A mention is not a recommendation. Only literal symbols are indexed; company tickers are not inferred. Returns are not calculated.',cancelled:'Cancelled',queued:'Queued',running:'Processing',done:'Complete',failed:'Failed',retry:'Retry',process:'Transcribe & summarize',discover:'Discover videos',unknownDate:'Unknown date',unknownCreator:'Unknown creator',savedTranscript:'Transcript saved',summarized:'Summarized',pending:'Pending',selected:'selected',scanBound:'Results cover this scan only. Older videos may be outside the scanned range.',missingDates:'Videos with unknown dates are excluded from date filters.',addedQueue:'Added to the processing queue.',savedToken:'Access token saved for this tab.',ready:'Ready',unavailable:'Not ready',cpu:'CPU',model:'Summary model',speech:'Speech transcription',media:'Video discovery',loading:'Loading…',scanning:'Scanning; resolving channel publication dates may take a few minutes.',viewLibrary:'View library →',transcripts:'transcripts',summaries:'summaries',tickers:'stock symbols',buy:'Buy',sell:'Sell',hold:'Hold',mention:'Mention',source:'Source',language:'Language',noMore:'Showing up to 1,000 results. Narrow by date or creator for older research.',invalidWindow:'The start date must not be later than the end date.'});
Object.assign(en,{feeds:'Followed feeds',alwaysLearning:'ALWAYS LEARNING',feedsTitle:'Keep up, without keeping watch.',feedsIntro:'New uploads are summarized automatically. Backfills and checkpoints survive restarts, so gaps are caught up when the server returns.',followHint:'Feeds are monitored for new uploads. Select existing videos below to summarize them.',dateWindow:'Since go-back time',sync:'Check feed & backfill',emptyFeeds:'Let your sources come to you.',emptyFeedsSub:'Paste a channel or playlist in New research to start following it.',pause:'Pause monitoring',resume:'Resume monitoring',monitoring:'Monitoring',paused:'Paused',backfill:'Backfilling',lastChecked:'Last checked',nextCheck:'Next check',never:'Not yet',since:'Go back to',pollInterval:'Checks are queued every',minutes:'minutes. Existing jobs finish before the next check.',feedNote:'Pausing stops future scans; already queued video processing continues.'});
Object.assign(zh,{feeds:'关注的频道',alwaysLearning:'持续积累',feedsTitle:'不必盯着，也能跟上。',feedsIntro:'自动总结新发布的视频。回溯进度和检查点持久保存，服务器重启后会继续补齐空缺。',followHint:'自动监控频道的新视频。在下方选择已有视频，然后点击总结。',dateWindow:'回溯时间之后',featureBatchText:'手动多选、选取最新 N 个，或指定回溯起点。',sync:'检查频道并补齐历史',emptyFeeds:'让信息主动来找你。',emptyFeedsSub:'在“新建研究”中粘贴频道或播放列表，即可开始自动关注。',pause:'暂停监控',resume:'恢复监控',monitoring:'监控中',paused:'已暂停',backfill:'回溯中',lastChecked:'上次检查',nextCheck:'下次检查',never:'尚未检查',since:'回溯至',pollInterval:'检查任务间隔',minutes:'分钟。已有任务完成后再执行下一次检查。',feedNote:'暂停只停止后续扫描；已入队的视频会继续处理。'});
Object.assign(en,{scanLimit:'Entries per page',loadMore:'Load more videos',scanBound:'More entries may be available. Load another page to browse older videos.',loadedCount:'videos displayed',scannedCount:'entries scanned',metadataIssues:'metadata warnings — expand for details',metadataUnavailable:'Metadata unavailable; processing will retry lookup.',endOfFeed:'Reached the end of the returned feed.'});
Object.assign(zh,{scanLimit:'每页扫描条数',loadMore:'加载更多视频',scanBound:'可能还有更多内容。加载下一页以浏览较早的视频。',loadedCount:'个视频已显示',scannedCount:'条记录已扫描',metadataIssues:'条元数据警告 — 展开查看详情',metadataUnavailable:'元数据暂不可用，处理时会重新尝试。',endOfFeed:'已到达返回列表的末尾。'});
Object.assign(en,{lookupQueued:'Video lookup queued. It runs separately from transcription and summarization.',lookupRunning:'Finding videos…',lookupSubmitting:'Requesting video list…',lookupFailed:'Could not load videos. Click Find videos to try again.',lookupDone:'Video list loaded.',lookupMissing:'This lookup no longer exists. Click Find videos again.'});
Object.assign(zh,{lookupQueued:'视频查询已入队，与转录和总结任务独立执行。',lookupRunning:'正在查找视频…',lookupSubmitting:'正在请求视频列表…',lookupFailed:'无法加载视频。请再次点击查找视频。',lookupDone:'视频列表已加载。',lookupMissing:'此查询任务已不存在，请再次点击查找视频。'});
Object.assign(en,{manual:'Manually',latest:'Last N videos',dateWindow:'Last N days',selectionCount:'Number (N)',selectionHint:'Select from loaded videos. Load more to include older results.',invalidSelectionCount:'Enter a positive whole number for N.'});
Object.assign(zh,{manual:'手动选择',latest:'最近 N 个视频',dateWindow:'最近 N 天',selectionCount:'数量（N）',selectionHint:'选择已加载的视频；加载更多可包含较早的视频。',invalidSelectionCount:'请输入大于 0 的整数 N。'});
Object.assign(en,{summarizeAction:'Summarize',addLibrary:'Add to Library',saveHint:'Save selected video links at any time. Saving automatically queues missing summaries; saving a feed also starts monitoring new uploads.',savedLibrary:'Video links saved. Missing summaries are queued automatically.',openResult:'View summary',followHint:'Paste a link and press Enter or Find videos. Select videos to save their links or summarize them.',viewLibrary:'View results →'});
Object.assign(zh,{summarizeAction:'总结',addLibrary:'添加到资料库',saveHint:'随时保存所选视频链接。保存后会自动排队生成缺失的总结，保存频道也会开启新视频监控。',savedLibrary:'视频链接已保存，缺失的总结已自动加入队列。',openResult:'查看总结',followHint:'粘贴链接后按回车或点击查找视频。选择视频后可保存链接或生成总结。',viewLibrary:'查看结果 →'});
Object.assign(en,{summarizeEligible:'Only selected videos without a summary or a queued/running job will be submitted.'});
Object.assign(zh,{summarizeEligible:'仅提交尚无总结且没有等待中或运行中任务的所选视频。'});
Object.assign(en,{llmConfig:'LLM configuration',closeDialog:'Close',llmIntro:'Use a local server or a public OpenAI-compatible Chat Completions API. Transcript content is sent to this endpoint.',llmEndpoint:'API base URL',llmModel:'Model name / alias',llmFormat:'API response format',llmLocal:'Local llama.cpp (JSON schema)',llmJson:'Compatible API (JSON mode)',llmText:'Compatible API (prompt-only JSON)',llmKey:'API key (optional for local servers)',llmClearKey:'Remove saved API key',llmApplyHint:'Applies to subsequent summaries. A summary already running keeps its configuration. Blank key preserves the saved key.',llmTest:'Test connection',llmKeySaved:'An API key is saved on the server.',llmKeyEmpty:'No API key is configured.',llmSaved:'LLM settings saved.',llmConnected:'Connected; selected model is listed. This checks model availability, not generation.',llmModelMissing:'Connected, but the selected model is not listed. Verify its name or alias.'});
Object.assign(zh,{llmConfig:'大模型配置',closeDialog:'关闭',llmIntro:'使用本地服务或兼容 OpenAI Chat Completions 的公共 API。视频原文将发送到此地址。',llmEndpoint:'API 基础地址',llmModel:'模型名称 / 别名',llmFormat:'API 返回格式',llmLocal:'本地 llama.cpp（JSON Schema）',llmJson:'兼容 API（JSON 模式）',llmText:'兼容 API（提示词要求 JSON）',llmKey:'API 密钥（本地服务可不填）',llmClearKey:'移除已保存的 API 密钥',llmApplyHint:'后续总结使用新配置，正在生成的总结保持原配置。密钥留空则保留已保存的密钥。',llmTest:'测试连接',llmKeySaved:'服务器已保存 API 密钥。',llmKeyEmpty:'尚未配置 API 密钥。',llmSaved:'大模型配置已保存。',llmConnected:'连接成功，已找到所选模型。此测试检查可用模型，不测试生成。',llmModelMissing:'连接成功，但列表中未找到所选模型。请核对名称或别名。'});
const t = key => (lang === 'zh' ? zh[key] : en[key]) || en[key] || key;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2,'0')}`;
const dateLabel = value => value ? new Date(value).toLocaleDateString(lang === 'zh' ? 'zh-CN':'en-US', {year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}) : t('unknownDate');
const empty = (heading, text = '') => `<div class="empty"><div class="empty-icon">▤</div><h2>${esc(heading)}</h2><p>${esc(text)}</p></div>`;
function notify(message, error = false) { const box = $('#notice'); box.textContent = message; box.classList.toggle('error', error); box.hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => box.hidden = true, error ? 18000 : 6000); }
async function request(path, body, raw = false) {
 const headers = {}; const token = sessionStorage.getItem('frame-token'); if (token) headers.Authorization = `Bearer ${token}`;
 if (body !== undefined) headers['Content-Type'] = 'application/json';
 const response = await fetch(path, {method:body === undefined ? 'GET':'POST',headers,body:body === undefined ? undefined:JSON.stringify(body)});
 if (!response.ok) { const data = await response.json().catch(() => ({})); if (response.status === 401 && currentView !== 'settings') showView('settings'); const error=new Error(data.error || `Request failed (${response.status})`);error.status=response.status;throw error; }
 return raw ? response : response.json();
}
function handle(fn) { return async event => { try { await fn(event); } catch (error) { notify(error.message, true); } }; }
function translate() {
 $('#llm-config').title=t('settings');
 $('#llm-config').setAttribute('aria-label',t('settings'));
 document.documentElement.lang = lang === 'zh' ? 'zh-CN':'en';
 $$('[data-i18n]').forEach(el => el.innerHTML = t(el.dataset.i18n));
 $('#language').textContent = lang === 'zh' ? 'EN':'中文';
 $('#breadcrumb').textContent = `${lang === 'zh' ? '工作区':'Workspace'} / ${t({discover:'newResearch',library:'library',queue:'queue',settings:'settings',feeds:'feeds',detail:'library'}[currentView])}`;
 if (discoveryResult) renderDiscovery(); if(libraryData) renderLibrary(); renderJobs(); if(video && currentView === 'detail') renderDetail(); if(statusData) renderStatus(); if(feedsData) renderFeeds();
}
function showView(view) {
 currentView = view; $$('.view').forEach(el => el.hidden = el.id !== `view-${view}`);
 $$('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view || (view === 'detail' && el.dataset.view === 'library')));
 $('#breadcrumb').textContent = `${lang === 'zh' ? '工作区':'Workspace'} / ${t({discover:'newResearch',library:'library',queue:'queue',settings:'settings',feeds:'feeds',detail:'library'}[view])}`;
 if(view === 'queue') poll().catch(e => notify(e.message,true));
 if(view === 'library') loadLibrary().catch(e => notify(e.message,true));
 if(view === 'feeds') loadFeeds().catch(e => notify(e.message,true));
 if(view === 'settings') loadStatus().catch(e => notify(e.message,true));
 window.scrollTo({top:0,behavior:'smooth'});
}
$$('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
$('#language').addEventListener('click', () => {lang = lang === 'en' ? 'zh':'en'; localStorage.setItem('frame-language',lang); translate();});
function lookupStatus(message, failed=false) {
 const status=$('#discovery-status');status.hidden=false;status.textContent=message;status.classList.toggle('error',failed);
}
function releaseDiscovery() {
 activeDiscovery=null;sessionStorage.removeItem('frame-discovery');$('#discover-button').disabled=false;$('#load-more').disabled=false;
}
$('#source-url').addEventListener('keydown',event=>{
 if(event.key==='Enter' && !event.isComposing) {
  event.preventDefault();if(!$('#discover-button').disabled)$('#discover-form').requestSubmit();
 }
});
$('#discover-form').addEventListener('submit',handle(async event => {
 event.preventDefault(); $('#discover-button').disabled = true;lookupStatus(t('lookupSubmitting'));
 try { const result = await request('/api/discover',{url:$('#source-url').value.trim(),limit:Number($('#scan-limit').value)}); discoverySince = null; activeDiscovery = result.job_id; sessionStorage.setItem('frame-discovery', activeDiscovery);lookupStatus(t('lookupQueued')); notify(t('scanning')); await poll(); }
 catch(error) { $('#discover-button').disabled = false;lookupStatus(error.message,true); throw error; }
}));
$('#load-more').addEventListener('click',handle(async()=>{
 if(activeDiscovery || !discoveryResult?.possibly_more) return;
 $('#load-more').disabled=true;
 try {
  const offset = discoveryResult.next_offset ?? ((discoveryResult.offset || 0) + (discoveryResult.scanned ?? discoveryResult.limit));
  const result=await request('/api/discover',{url:discoveryResult.source,limit:discoveryResult.limit,offset});
  activeDiscovery=result.job_id;sessionStorage.setItem('frame-discovery',activeDiscovery);$('#discover-button').disabled=true;
  lookupStatus(t('lookupQueued'));notify(t('scanning'));await poll();
 } catch(error) {$('#load-more').disabled=false;throw error;}
}));
function acceptDiscoveryPage(page) {
 const append = (page.offset || 0)>0 && discoveryResult?.source===page.source;
 const previous = append ? discoveryResult : null;
 if(!append) selected.clear();
 const videos = new Map((append ? discovery : []).map(v=>[v.id,v]));
 page.videos.forEach(v=>videos.set(v.id,v));
 discovery=[...videos.values()].sort((a,b)=>(b.published_at || '').localeCompare(a.published_at || ''));
 discoveryResult={...page,scanned_total:(previous?.scanned_total ?? previous?.scanned ?? 0)+(page.scanned ?? page.videos.length),warnings:[...(previous?.warnings || []),...(page.warnings || [])]};
}
function renderDiscovery() {
 $('#discovery').hidden = false; $('#discovery-title').textContent = `${discovery.length} ${t('videos')}`;
 $('#scan-note').textContent = discoveryResult.possibly_more ? t('scanBound') : t('endOfFeed');
 $('#load-more').hidden = !discoveryResult.possibly_more; $('#load-more').disabled = !!activeDiscovery;
 $('#discovery-count').textContent = `${discovery.length} ${t('loadedCount')} · ${discoveryResult.scanned_total ?? discoveryResult.scanned ?? discovery.length} ${t('scannedCount')}`;
 const warnings=[...(discoveryResult.warnings || []), ...(discovery.some(v => !v.published_at) ? [t('missingDates')] : [])];
 $('#discovery-warning-panel').hidden=!warnings.length;$('#discovery-warning-summary').textContent=`${warnings.length} ${t('metadataIssues')}`;
 $('#discovery-warnings').innerHTML=warnings.map(w=>`<li>${esc(w)}</li>`).join('');
 $('#discovery-list').innerHTML = discovery.length ? discovery.map(v => `<label class="video-row"><input type="checkbox" value="${esc(v.id)}" ${selected.has(v.id) ? 'checked':''}><span class="video-icon">▶</span><span class="video-main"><span class="video-title">${esc(v.title)}</span><span class="video-meta">${esc(v.creator || t('unknownCreator'))} · ${esc(dateLabel(v.published_at))}${v.duration ? ' · '+clock(v.duration):''}</span>${summaryReady(v.id) ? `<button type="button" class="text-button" data-result="${esc(v.id)}">${esc(t('openResult'))}</button>`:''}${v.metadata_error ? `<span class="metadata-warning">${esc(t('metadataUnavailable'))}</span>`:''}</span><a class="text-button" href="${esc(v.url)}" target="_blank" rel="noopener noreferrer" aria-label="YouTube">↗</a></label>`).join('') : empty(t('noVideos'));
 $$('[data-result]').forEach(button=>button.addEventListener('click',handle(async event=>{event.preventDefault();event.stopPropagation();await openVideo(button.dataset.result);})));
 $$('#discovery-list input').forEach(box => box.addEventListener('change', () => {if(box.checked) selected.add(box.value); else selected.delete(box.value); $('#selection-mode').value='manual'; selectionControls(); updateSelected();}));
 updateSelected();
}
let submittingSummaries=false, savingLibrary=false;
function canSummarize(id) {return !summaryReady(id) && !jobs.some(j=>j.video_id===id && j.kind==='process' && ['queued','running'].includes(j.status));}
function summaryReady(id) {return discovery.some(v=>v.id===id && v.summary_ready) || jobs.some(j=>j.video_id===id && j.kind==='process' && j.status==='done');}
function updateSelected() {
 $('#selected-count').textContent = `${selected.size} / ${discovery.length} ${t('selected')}`;
 $('#process-selected').disabled = submittingSummaries || ![...selected].some(canSummarize);
 $('#process-selected').title = t('summarizeEligible');
 $('#save-selected').disabled = savingLibrary || !selected.size;
}

function selectionControls() {
 $('#selection-count').disabled=$('#selection-mode').value==='manual';
}
function selectionNumber() {
 const value=$('#selection-count').value;
 const count=Number(value);
 if(!value.trim() || !Number.isSafeInteger(count) || count<1) throw new Error(t('invalidSelectionCount'));
 return count;
}
function applySelection() {
 selectionControls(); const mode=$('#selection-mode').value;
 if(mode!=='manual') {
  try {
   const count=selectionNumber();
   if(mode==='latest') selected=new Set(discovery.slice(0,count).map(v=>v.id));
   if(mode==='window') {
    if(!discoverySince) discoverySince=new Date().toISOString();
    const until=new Date(discoverySince).getTime();
    const from=until-count*86400000;
    if(!Number.isFinite(from) || from < -62135596800000) throw new Error(t('invalidSelectionCount'));
    selected=new Set(discovery.filter(v=>v.published_at && new Date(v.published_at).getTime()>=from && new Date(v.published_at).getTime()<=until).map(v=>v.id));
   }
  } catch(error) {
   selected.clear();if(discoveryResult)renderDiscovery();else updateSelected();throw error;
  }
 }
 if(discoveryResult)renderDiscovery();
}
['selection-mode','selection-count'].forEach(id=>$('#'+id).addEventListener('change',handle(()=>{discoverySince=null;applySelection();})));
$('#select-all').addEventListener('click',()=> {selected=new Set(discovery.map(v=>v.id)); $('#selection-mode').value='manual'; selectionControls(); renderDiscovery();});
$('#clear-selection').addEventListener('click',()=> {selected.clear(); $('#selection-mode').value='manual'; selectionControls(); renderDiscovery();});
$('#process-selected').addEventListener('click',handle(async()=>{
 if(submittingSummaries)return;
 const ids=[...selected].filter(canSummarize);if(!ids.length)return;
 submittingSummaries=true;updateSelected();
 try {
  for(let i=0;i<ids.length;i+=500) {
   const batch=ids.slice(i,i+500), result=await request('/api/process',{ids:batch});
   // Keep accepted jobs visible even if the next status refresh fails.
   batch.forEach((id,index)=>{if(!jobs.some(j=>j.id===result.job_ids[index]))jobs.push({id:result.job_ids[index],video_id:id,kind:'process',status:'queued',created_at:new Date().toISOString(),updated_at:new Date().toISOString()});});
  }
  renderJobs();notify(t('addedQueue'));await poll();
 } finally {submittingSummaries=false;updateSelected();}
}));
$('#save-selected').addEventListener('click',handle(async()=>{
 if(savingLibrary || !discoveryResult)return;
 const ids=[...selected], source=discoveryResult.source;
 if(!ids.length)return;
 savingLibrary=true;updateSelected();
 try {
  await request('/api/library',{ids,url:source});
  for(const item of discovery)if(ids.includes(item.id))item.saved_sources=[...new Set([...(item.saved_sources || []),source])];
  notify(t('savedLibrary'));await loadLibrary();await poll();
 } finally {savingLibrary=false;updateSelected();}
}));
function searchParams() {const params=new URLSearchParams(); for(const [id,key] of [['query','q'],['ticker','ticker'],['creator','creator'],['category','category'],['search-from','from'],['search-to','to']]) {const value=$('#'+id).value.trim(); if(value) params.set(key,value);} return params;}
async function loadLibrary() {libraryData=await request('/api/library?'+searchParams()); renderLibrary();}
Object.assign(en,{ungrouped:'Ungrouped',deleteFeed:'Delete feed / stop watching',notWatching:'Not watching',deleteSummary:'Delete summary',feedStopped:'Monitoring stopped. Saved summaries are kept.',summaryDeleted:'Summary removed from Library. Transcript retained.',noSavedVideos:'No saved videos in this feed yet.'});
Object.assign(zh,{ungrouped:'未分组',deleteFeed:'删除订阅 / 停止监控',notWatching:'未监控',deleteSummary:'删除总结',feedStopped:'已停止监控，已保存的总结将保留。',summaryDeleted:'已从资料库删除总结，原文已保留。',noSavedVideos:'此来源暂无已保存的视频。'});
function renderLibrary() {
 const old=$('#creator').value; $('#creator').innerHTML=`<option value="">${esc(t('allCreators'))}</option>`+libraryData.creators.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join(''); $('#creator').value=old;
 $('#library-count').textContent=libraryData.stats.videos;
 $('#stats').innerHTML=['videos','transcripts','summaries','tickers'].map(key=>`<div class="stat"><strong>${libraryData.stats[key]}</strong><span>${esc(t(key))}</span></div>`).join('');
 const byId=new Map(libraryData.videos.map(v=>[v.id,v])), grouped=new Set();
 const card=v=>`<article class="library-card"><button class="library-open" data-video="${esc(v.id)}"><span class="badge">${esc(t(v.category))}</span><span class="badge">${esc(v.language || '—')}</span><h3>${esc(v.title)}</h3><div class="video-meta">${esc(v.creator || t('unknownCreator'))} · ${esc(dateLabel(v.published_at))}</div><p>${esc(v.summary_preview || t('pending'))}</p></button><button class="text-button danger" data-delete-summary="${esc(v.id)}">${esc(t('deleteSummary'))}</button></article>`;
 const sections=(libraryData.groups || []).map(group=>{
  const items=group.video_ids.map(id=>byId.get(id)).filter(Boolean);items.forEach(v=>grouped.add(v.id));
  if(searchParams().size && !items.length)return '';
  return `<section class="library-group"><div class="section-heading"><div><h2>${esc(group.title)}</h2><p class="muted">${esc(group.url)}</p><span class="badge">${esc(t(group.enabled ? 'monitoring':'notWatching'))}</span></div>${group.enabled ? `<button class="secondary" data-stop-feed="${esc(group.id)}">${esc(t('deleteFeed'))}</button>`:''}</div>${items.map(card).join('') || `<p class="muted">${esc(t('noSavedVideos'))}</p>`}</section>`;
 });
 const ungrouped=libraryData.videos.filter(v=>!grouped.has(v.id));
 if(ungrouped.length)sections.push(`<section class="library-group"><h2>${esc(t('ungrouped'))}</h2>${ungrouped.map(card).join('')}</section>`);
 $('#library-list').innerHTML=sections.join('') || empty(t(searchParams().size ? 'emptySearch':'emptyLibrary'),t(searchParams().size ? 'emptySearchSub':'emptyLibrarySub'));
 $$('[data-video]').forEach(button=>button.addEventListener('click',handle(()=>openVideo(button.dataset.video))));
 $$('[data-stop-feed]').forEach(button=>button.addEventListener('click',handle(async()=>{
  button.disabled=true;try {await request('/api/feeds',{id:button.dataset.stopFeed,enabled:false});await loadLibrary();await loadFeeds();notify(t('feedStopped'));}finally{button.disabled=false;}
 })));
 $$('[data-delete-summary]').forEach(button=>button.addEventListener('click',handle(async()=>{
  button.disabled=true;try {const id=button.dataset.deleteSummary;await request('/api/library/delete-summary',{id});
   discovery.filter(v=>v.id===id).forEach(v=>{v.summary_ready=false;v.saved_sources=[];});jobs=jobs.filter(j=>j.video_id!==id);updateSelected();
   if(video?.id===id)video=null;await loadLibrary();await poll();notify(t('summaryDeleted'));
  }finally{button.disabled=false;}
 })));
}
$('#search-form').addEventListener('submit',handle(async e=>{e.preventDefault(); if($('#search-from').value && $('#search-to').value && $('#search-from').value>$('#search-to').value) throw new Error(t('invalidWindow')); await loadLibrary();}));
$('#refresh-library').addEventListener('click',handle(loadLibrary));
$('#reset-search').addEventListener('click',handle(async()=>{$('#search-form').reset();await loadLibrary();}));
Object.assign(en,{queueRunning:'Running now',queueWaiting:'Waiting',queueHistory:'Recent results (up to 100)',queuePosition:'Position',queueAge:'Since queued',stageAge:'Since last update',queueLive:'Updates every 5 seconds. Discovery runs separately; feed scans and video processing share one queue.',queueOffline:'Queue refresh failed. Displayed status may be out of date.',openQueue:'Open queue →',queueIdle:'No active jobs',queueProcessing:'Video / feed queue',queueDiscovery:'Discovery queue'});
Object.assign(zh,{queueRunning:'正在处理',queueWaiting:'等待处理',queueHistory:'最近结果（最多 100 条）',queuePosition:'排队位置',queueAge:'入队至今',stageAge:'距上次进度更新',queueLive:'每 5 秒刷新。视频查询独立运行；频道扫描与视频处理共用一个队列。',queueOffline:'队列刷新失败，当前状态可能已过期。',openQueue:'查看队列 →',queueIdle:'暂无活动任务',queueProcessing:'视频 / 频道处理队列',queueDiscovery:'视频查询队列'});
function jobAge(value) {const seconds=Math.max(0,Math.floor((Date.now()-new Date(value).getTime())/1000));return Number.isFinite(seconds) ? clock(seconds) : '—';}
$('#queue-overview').addEventListener('click',()=>showView('queue'));
$('#refresh-queue').addEventListener('click',handle(poll));
function renderJobs() {
 const queueJobs=jobs.filter(j=>j.kind!=='discover');
 const running=queueJobs.filter(j=>j.status==='running'), waiting=queueJobs.filter(j=>j.status==='queued');
 const history=queueJobs.filter(j=>!['running','queued'].includes(j.status));
 const title=j=>j.title || discovery.find(v=>v.id===j.video_id)?.title || j.source_url || j.video_id || t(j.kind);
 $('#queue-count').textContent=running.length+waiting.length;
 $('#queue-overview').hidden=!(running.length+waiting.length);
 $('#queue-overview').textContent=`${t('queueRunning')}: ${running.length} · ${t('queueWaiting')}: ${waiting.length}${running.length ? ' · '+title(running[0])+' — '+(running[0].progress || t('running')):''} · ${t('openQueue')}`;
 $('#queue-stats').innerHTML=[['queueRunning',running.length],['queueWaiting',waiting.length],['failed',history.filter(j=>j.status==='failed').length],['done',history.filter(j=>j.status==='done').length]].map(([key,count])=>`<div class="stat"><strong>${count}</strong><span>${esc(t(key))}</span></div>`).join('');
 const card=j=>`<article class="job"><div class="job-top"><div><span class="badge ${esc(j.status)}">${esc(t(j.status))}</span><strong>${esc(t(j.kind))}</strong>${j.status==='queued' ? `<span class="badge">${esc(t('queuePosition'))} ${esc(j.queue_position || '—')} · ${esc(t(j.lane==='discovery' ? 'queueDiscovery':'queueProcessing'))}</span>`:''}</div>${j.status==='failed' ? `<button class="secondary" data-retry="${esc(j.id)}">${esc(t('retry'))}</button>`:''}${j.status==='done'&&j.video_id ? `<button class="text-button" data-open="${esc(j.video_id)}">${esc(t('viewLibrary'))}</button>`:''}</div><h3>${esc(title(j))}</h3>${j.creator ? `<p>${esc(j.creator)}</p>`:''}${j.video_id ? `<a class="text-button" href="https://www.youtube.com/watch?v=${encodeURIComponent(j.video_id)}" target="_blank" rel="noopener noreferrer">${esc(t('watchOriginal'))}</a>`:''}<p>${esc(j.error || j.progress || t('queued'))}</p><p>${['running','queued'].includes(j.status) ? `${esc(t('queueAge'))}: ${jobAge(j.created_at)} · ${esc(t('stageAge'))}: ${jobAge(j.updated_at)}` : esc(new Date(j.updated_at || j.created_at).toLocaleString(lang==='zh'?'zh-CN':'en-US'))}</p></article>`;
 $('#job-list').innerHTML=queueJobs.length ? [[t('queueRunning'),running],[t('queueWaiting'),waiting],[t('queueHistory'),history]].filter(([,items])=>items.length).map(([heading,items])=>`<section class="queue-section"><h2>${esc(heading)} (${items.length})</h2>${items.map(card).join('')}</section>`).join('') : empty(t('emptyQueue'),t('emptyQueueSub'));
 $$('[data-retry]').forEach(button=>button.addEventListener('click',handle(async()=>{button.disabled=true; try {const result=await request('/api/retry',{id:button.dataset.retry}); const job=jobs.find(j=>j.id===button.dataset.retry); if(job.kind==='discover') {activeDiscovery=result.job_id;sessionStorage.setItem('frame-discovery',activeDiscovery);$('#discover-button').disabled=true;} await poll();} finally {button.disabled=false;}})));
 $$('[data-open]').forEach(button=>button.addEventListener('click',handle(()=>openVideo(button.dataset.open))));
}
Object.assign(en,{saveDraft:'Add to Library',draftNotice:'Temporary research. Save to Library to keep it after the app restarts.',sessionExpired:'The app restarted. Unsaved research and jobs have expired; your Library is preserved.',queueIntro:'One job at a time. Unsaved work lasts until the app restarts; Library jobs survive restarts.',summaryDeleted:'Removed from Library. Retained transcript and slides are temporary until the app restarts.'});
Object.assign(zh,{saveDraft:'加入资料库',draftNotice:'临时研究。加入资料库后，应用重启仍可保留。',sessionExpired:'应用已重启，未保存的研究和任务已过期，资料库仍保留。',queueIntro:'串行处理。未保存的任务仅保留到应用重启；资料库任务可在重启后恢复。',summaryDeleted:'已从资料库移除，保留的原文和幻灯片仅在应用重启前可用。'});
let serverSession = sessionStorage.getItem('frame-server-session');
function acceptServerSession(ident) {
 if(!ident)return;
 const changed=serverSession && serverSession!==ident;
 serverSession=ident;sessionStorage.setItem('frame-server-session',ident);
 if(!changed)return;
 releaseDiscovery();discovery=[];discoveryResult=null;discoverySince=null;selected.clear();jobs=[];video=null;++slideRequest;
 $('#discovery').hidden=true;$('#discovery-list').innerHTML='';$('#detail-content').innerHTML='';$('#detail-header').innerHTML='';
 if(currentView==='detail')showView('discover');
 updateSelected();notify(t('sessionExpired'));
}
async function poll() {
 if(polling) return; polling=true;
 try { const data=await request('/api/jobs'); acceptServerSession(data.session_id); const old=jobs; jobs=data.jobs;$('#queue-status').textContent=t('queueLive');for(const item of discovery) {if(jobs.some(j=>j.video_id===item.id && j.kind==='process' && j.status==='done'))item.summary_ready=true;} renderJobs();updateSelected(); if(currentView==='feeds') await loadFeeds();
   if(activeDiscovery) {const job=jobs.find(j=>j.id===activeDiscovery) || await request('/api/jobs/'+activeDiscovery); if(job?.status==='queued' || job?.status==='running') lookupStatus(job.status==='queued' ? t('lookupQueued') : `${t('lookupRunning')} ${job.progress || ''}`); if(job?.status==='done') {lookupStatus(t('lookupDone'));acceptDiscoveryPage(job.result); activeDiscovery=null;sessionStorage.removeItem('frame-discovery');$('#discover-button').disabled=false;applySelection();renderDiscovery();if(currentView==='discover') $('#discovery').scrollIntoView({behavior:'smooth'});} else if(job?.status==='failed') {lookupStatus(`${t('lookupFailed')} ${job.error || ''}`,true);notify(job.error,true);activeDiscovery=null;sessionStorage.removeItem('frame-discovery');$('#discover-button').disabled=false;$('#load-more').disabled=false;}}
   const finished=jobs.filter(j=>j.status==='done' && old.some(o=>o.id===j.id && o.status!=='done'));
   if(currentView==='detail' && activeTab==='slides' && jobs.some(j=>['process','slides'].includes(j.kind) && j.video_id===video?.id && j.status==='failed' && old.some(o=>o.id===j.id && o.status!=='failed')))renderDetail();
   if(finished.some(j=>j.video_id)) {if(discoveryResult)renderDiscovery();await loadLibrary(); if(currentView==='detail' && finished.some(j=>j.video_id===video?.id)) await openVideo(video.id,false);}
 } catch(error) {
  $('#queue-status').textContent=t('queueOffline');
  if(!$('#queue-overview').hidden)$('#queue-overview').textContent=t('queueOffline')+' '+t('openQueue');
  if(activeDiscovery) {lookupStatus(error.status===404 ? t('lookupMissing') : error.message,true);if(error.status===404)releaseDiscovery();}
  throw error;
 } finally {polling=false;}
}
async function openVideo(id, changeView=true) { if(changeView || video?.id!==id)slideIndex=0;video=await request('/api/videos/'+encodeURIComponent(id)); if(changeView) {activeTab='summary';showView('detail');} renderDetail(); }
function inlineMarkdown(text) {return esc(text).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\[([^\]]+)\]\((https:\/\/[^\s<]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');}
function markdown(text) {
 const lines=text.split('\n');let output='',list=false,table=false;
 for(let i=0;i<lines.length;i++) { const line=lines[i];
  if(line.trim().startsWith('|')) {if(list){output+='</ul>';list=false;}if(!table){output+='<table>';table=true;}if(/^\s*\|[\s:|\-]+\|\s*$/.test(line))continue;output+='<tr>'+line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>'<td>'+inlineMarkdown(cell.trim())+'</td>').join('')+'</tr>';continue;}
  if(table){output+='</table>';table=false;}
  const bullet=/^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);if(bullet){if(!list){output+='<ul>';list=true;}output+='<li>'+inlineMarkdown(bullet[1])+'</li>';continue;}if(list){output+='</ul>';list=false;}
  const heading=/^(#{1,6})\s+(.+)$/.exec(line);if(heading){const level=Math.min(heading[1].length+1,3);output+=`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`;}else if(line.trim())output+='<p>'+inlineMarkdown(line)+'</p>';
 }
 return output+(list?'</ul>':'')+(table?'</table>':'');
}
Object.assign(en,{process:'Transcribe, summarize & capture slides',slideFirst:'Beginning',slidePrevious:'Back',slideNext:'Forward',slideLast:'End',slides:'Slides',slideKindTable:'Table',slideKindChart:'Chart / graph',slideKindList:'Bulleted / numbered list',scanSlides:'Scan slides',slideMinimum:'Minimum stable seconds',slideHelp:'Capture tables (2×2 or larger), charts/graphs, and lists with at least 3 bulleted or numbered items. Content must stay stable. Chat/comment screens are excluded heuristically.',noSlides:'No stable slide candidates found.',slidesUnscanned:'Scan this video to find slides.',slideScrub:'Scrub slides',slideImage:'Slide from the original video',slideLoading:'Loading slide…'});
Object.assign(zh,{process:'转录、总结与捕获幻灯片',slideFirst:'第一张',slidePrevious:'上一张',slideNext:'下一张',slideLast:'最后一张',slides:'幻灯片',slideKindTable:'表格',slideKindChart:'图表',slideKindList:'项目符号 / 编号列表',scanSlides:'扫描幻灯片',slideMinimum:'最短稳定秒数',slideHelp:'捕获至少 2×2 的表格、图表，以及至少 3 项的项目符号或编号列表。内容须保持稳定，通过启发式规则过滤聊天与评论界面。',noSlides:'未找到稳定的幻灯片候选。',slidesUnscanned:'扫描此视频以查找幻灯片。',slideScrub:'浏览幻灯片',slideImage:'原视频幻灯片',slideLoading:'正在加载幻灯片…'});
let slideRequest = 0, slideIndex = 0;
async function showSlide(index) {
 const count=video?.slides?.length || 0;if(!count || !Number.isFinite(index))return;
 index=Math.max(0,Math.min(count-1,Math.trunc(index)));slideIndex=index;
 const slide=video.slides[index];
 $('#slide-first').disabled=$('#slide-previous').disabled=index===0;
 $('#slide-next').disabled=$('#slide-last').disabled=index===count-1;
 const ident=video.id, version=++slideRequest;
 const kindKey={table:'slideKindTable',chart:'slideKindChart',list:'slideKindList'}[slide.content_kind];
 $('#slide-position').textContent=`${kindKey ? t(kindKey)+' · ' : ''}${index+1} / ${video.slides.length} · ${clock(slide.start)} – ${clock(slide.end)}`;
 $('#slide-original').href=`${video.url}&t=${Math.floor(slide.timestamp)}s`;
 $('#slide-image').hidden=true;$('#slide-message').textContent=t('slideLoading');
 try {
  const result=await request(`/api/slides/${encodeURIComponent(ident)}/${slide.position}`);
  if(version!==slideRequest || activeTab!=='slides' || video?.id!==ident)return;
  $('#slide-image').src=result.image;$('#slide-image').hidden=false;$('#slide-message').textContent='';
 } catch(error) {if(version===slideRequest && activeTab==='slides' && video?.id===ident)$('#slide-message').textContent=error.message;}
}
function renderSlides(content) {
 const items=video.slides || [], busy=jobs.some(j=>['process','slides'].includes(j.kind) && j.video_id===video.id && ['queued','running'].includes(j.status));
 content.innerHTML=`<p class="muted">${esc(t('slideHelp'))}</p><form id="slide-form" class="detail-actions"><label>${esc(t('slideMinimum'))} <input id="slide-minimum" type="number" min="1" max="60" step="1" value="${video.slide_scan?.minimum || 4}" required></label><button class="secondary" ${busy?'disabled':''}>${esc(t(busy?'queued':'scanSlides'))}</button></form>`+
 (items.length ? `<div class="slide-viewer"><div class="slide-controls" role="group" aria-label="${esc(t('slideScrub'))}">${[['first','⏮','slideFirst'],['previous','◀','slidePrevious'],['next','▶','slideNext'],['last','⏭','slideLast']].map(([id,icon,label])=>`<button type="button" class="secondary" id="slide-${id}" aria-label="${esc(t(label))}" title="${esc(t(label))}">${icon}<span>${esc(t(label))}</span></button>`).join('')}</div><p id="slide-position"></p><p id="slide-message" role="status"></p><img id="slide-image" alt="${esc(t('slideImage'))}" hidden><a id="slide-original" target="_blank" rel="noopener noreferrer">${esc(t('watchOriginal'))}</a></div>` : empty(t(video.slide_scan?'noSlides':'slidesUnscanned')));
 $('#slide-form').addEventListener('submit',handle(async event=>{
  event.preventDefault();const button=$('#slide-form button');button.disabled=true;
  try {await request('/api/slides',{id:video.id,minimum:Number($('#slide-minimum').value)});notify(t('addedQueue'));await poll();if(activeTab==='slides')renderDetail();}
  finally {button.disabled=false;}
 }));
 if(items.length){
  for(const [id,target] of [['first',()=>0],['previous',()=>slideIndex-1],['next',()=>slideIndex+1],['last',()=>items.length-1]])$('#slide-'+id).addEventListener('click',()=>showSlide(target()));
  showSlide(Math.min(slideIndex,items.length-1));
 }
}
function renderDetail() {
 $('#save-draft').hidden=!!video.saved;$('#draft-notice').hidden=!!video.saved;$('#draft-notice').textContent=t('draftNotice');
 $('#detail-header').innerHTML=`<h1>${esc(video.title)}</h1><div class="video-meta">${esc(video.creator)} · ${esc(dateLabel(video.published_at))} · ${esc(video.language || '—')} <span class="badge">${esc(t(video.category))}</span></div>`;
 $('#watch-video').href=video.url;
 $$('.tab').forEach(button=>button.classList.toggle('active',button.dataset.tab===activeTab));
 const content=$('#detail-content');
 ++slideRequest;
 if(activeTab==='slides')renderSlides(content);
 if(activeTab==='summary')content.innerHTML=video.summary ? `<div class="prose">${markdown(video.summary.markdown)}</div><p class="muted">${esc(video.summary.model)} · ${esc(dateLabel(video.summary.created_at))}</p>` : empty(t('noSummary'));
 if(activeTab==='transcript')content.innerHTML=video.transcript ? `<p class="muted">${esc(t('source'))}: ${esc(video.transcript.source)} · ${esc(video.transcript.language)}</p>`+video.transcript.segments.map(s=>`<div class="transcript-line"><a href="${esc(video.url)}&t=${Math.floor(s.start)}s" target="_blank" rel="noopener noreferrer">${clock(s.start)}</a><span>${esc(s.text)}</span></div>`).join('') : empty(t('noTranscript'));
 if(activeTab==='mentions')content.innerHTML=`<p class="muted">${esc(t('mentionNote'))}</p>`+(video.mentions.length ? video.mentions.map(m=>`<div class="mention"><strong>${esc(m.ticker)}</strong> <span class="badge">${esc(t(m.stance))}</span><a href="${esc(video.url)}&t=${Math.floor(m.timestamp)}s" target="_blank" rel="noopener noreferrer">${clock(m.timestamp)}</a><blockquote>${esc(m.evidence)}</blockquote></div>`).join('') : empty(t('noMentions')));
}

$$('[data-tab]').forEach(button=>button.addEventListener('click',()=>{activeTab=button.dataset.tab;renderDetail();}));
$('#back-library').addEventListener('click',()=>showView('library'));
$('#save-draft').addEventListener('click',handle(async()=>{
 const id=video.id,url=video.url,button=$('#save-draft');button.disabled=true;
 try {await request('/api/library',{ids:[id],url});await openVideo(id,false);await loadLibrary();await poll();notify(t('savedLibrary'));}
 finally {button.disabled=false;}
}));
$('#retry-summary').addEventListener('click',handle(async()=>{await request('/api/process',{ids:[video.id],force:true});notify(t('addedQueue'));await poll();}));
$('#export-video').addEventListener('click',handle(async()=>{const response=await request('/api/export/'+video.id,undefined,true);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=video.id+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
async function loadStatus(){statusData=await request('/api/status');renderStatus();}
function renderStatus(){const s=statusData;$('#server-status').innerHTML=[['model',s.inference&&s.model_ready,s.model],['speech',s.whisper,`${s.whisper_model} · ${s.device}`],['media',s.yt_dlp,'yt-dlp']].map(([key,ok,label])=>`<div class="status-item"><strong>${esc(t(key))}</strong><span>${esc(label)}</span><br><span class="badge ${ok?'':'failed'}">${esc(t(ok?'ready':'unavailable'))}</span></div>`).join('');}
$('#refresh-status').addEventListener('click',handle(loadStatus));
$('#token-form').addEventListener('submit',handle(async e=>{e.preventDefault();sessionStorage.setItem('frame-token',$('#access-token').value.trim());$('#access-token').value='';notify(t('savedToken'));await loadStatus();await poll();await loadLibrary();}));
$('#selection-count').value='5';selectionControls();
$('#discover-button').disabled=!!activeDiscovery;if(activeDiscovery)lookupStatus(t('lookupQueued'));
translate();
Promise.all([loadLibrary(),poll()]).catch(error=>notify(error.message,true));
setInterval(()=>poll().catch(error=>{if(currentView!=='settings')notify(error.message,true);}),5000);

async function loadFeeds(){feedsData=await request('/api/feeds');renderFeeds();}
function renderFeeds(){
 $('#feed-interval').textContent=`${t('pollInterval')} ${feedsData.poll_minutes} ${t('minutes')}`;
 $('#feed-list').innerHTML=feedsData.feeds.length ? feedsData.feeds.map(f=>`<article class="job"><div class="job-top"><div><span class="badge ${f.enabled?'':'queued'}">${esc(t(!f.enabled?'paused':f.scan_started?'backfill':'monitoring'))}</span><a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.url.replace('https://www.youtube.com/',''))}</a></div><button class="secondary" data-feed="${esc(f.id)}" data-enabled="${f.enabled?'0':'1'}">${esc(t(f.enabled?'pause':'resume'))}</button></div><p>${esc(t('since'))}: ${esc(new Date(f.since).toLocaleString())} · ${f.discovered} ${esc(t('videos'))}</p><p>${esc(t('lastChecked'))}: ${esc(f.last_checked ? new Date(f.last_checked).toLocaleString():t('never'))} · ${esc(t('nextCheck'))}: ${esc(new Date(f.next_scan).toLocaleString())}</p>${f.last_error?`<p>${esc(f.last_error)}</p>`:''}<p>${esc(t('feedNote'))}</p></article>`).join('') : empty(t('emptyFeeds'),t('emptyFeedsSub'));
 $$('[data-feed]').forEach(button=>button.addEventListener('click',handle(async()=>{await request('/api/feeds',{id:button.dataset.feed,enabled:button.dataset.enabled==='1'});await loadFeeds();})));
}

function llmFormValues(){return {endpoint:$('#llm-endpoint').value.trim(),model:$('#llm-model').value.trim(),format:$('#llm-format').value,api_key:$('#llm-key').value,clear_key:$('#llm-clear-key').checked};}
function llmBusy(value){$('#llm-save').disabled=value;$('#llm-test').disabled=value;}
$('#llm-config').addEventListener('click',handle(async()=>{
 const settings=await request('/api/llm-settings');
 $('#llm-endpoint').value=settings.endpoint;$('#llm-model').value=settings.model;$('#llm-format').value=settings.format;
 $('#llm-key').value='';$('#llm-clear-key').checked=false;$('#llm-message').textContent='';
 $('#llm-key-status').textContent=t(settings.key_configured?'llmKeySaved':'llmKeyEmpty');
 $('#llm-dialog').showModal();
}));
$('#llm-close').addEventListener('click',()=>$('#llm-dialog').close());
$('#llm-dialog').addEventListener('close',()=>{$('#llm-key').value='';});
$('#llm-form').addEventListener('submit',async event=>{
 event.preventDefault();if($('#llm-save').disabled)return;llmBusy(true);
 try {await request('/api/llm-settings',llmFormValues());$('#llm-key').value='';$('#llm-dialog').close();notify(t('llmSaved'));statusData=null;}
 catch(error){$('#llm-message').textContent=error.message;}finally{llmBusy(false);}
});
$('#llm-test').addEventListener('click',async()=>{
 if(!$('#llm-form').reportValidity())return;llmBusy(true);$('#llm-message').textContent=t('loading');
 try {const result=await request('/api/llm-settings/test',llmFormValues());$('#llm-message').textContent=t(result.model_ready?'llmConnected':'llmModelMissing');}
 catch(error){$('#llm-message').textContent=error.message;}finally{llmBusy(false);}
});
