/* Run only after the controlled benchmark writes optimisation-ui-ready.json.
 * Captures live UI evidence; never reads salts, credentials or raw records.
 * Install the pinned Playwright package in the CI job. No host paths are used.
 */
const fs=require('fs');
const path=require('path');
const crypto=require('crypto');

const root=path.resolve(__dirname,'..');
function optionsFromArgs(args){
  const options={};
  const names=new Set(['ready-file','done-file','evidence-dir','chrome-channel','chrome-executable','budget-ms']);
  for(let i=0;i<args.length;i++){
    if(args[i]==='--help'){options.help=true;continue;}
    const name=args[i].replace(/^--/,'');
    if(!args[i].startsWith('--')||!names.has(name)||!args[i+1]||args[i+1].startsWith('--'))throw new Error('Unknown or incomplete capture argument: '+args[i]);
    options[name]=args[++i];
  }
  return options;
}
const options=optionsFromArgs(process.argv.slice(2));
if(options.help){console.log('Usage: node tests/capture_optimisation_ui.cjs --ready-file FILE --done-file FILE --evidence-dir DIRECTORY [--chrome-channel chrome] [--chrome-executable FILE] [--budget-ms 120000]');process.exit(0);}
const {chromium}=require('playwright');
const readyPath=path.resolve(options['ready-file']||process.env.EPISCOPE_UI_READY_FILE||path.join(root,'work','optimisation-ui-ready.json'));
const donePath=path.resolve(options['done-file']||process.env.EPISCOPE_UI_DONE_FILE||path.join(root,'work','optimisation-ui-captured'));
const chromeChannel=options['chrome-channel']||process.env.EPISCOPE_BROWSER_CHANNEL||'chrome';
const chromeExecutable=options['chrome-executable']||process.env.EPISCOPE_CHROME_EXECUTABLE;
const budgetMs=Number(options['budget-ms']||120000);
if(!Number.isFinite(budgetMs)||budgetMs<15000||budgetMs>120000)throw new Error('Capture budget must be between 15000 and 120000 milliseconds.');
const expected=['join_before_sort_merge','join_after_broadcast','pruning_before_opaque_predicate','pruning_after_native_predicate'];
const operatorNames=['SortMergeJoin','BroadcastHashJoin','BatchEvalPython','FileScan','Scan parquet'];
const stamp=new Date().toISOString().replace(/[:.]/g,'-');
const evidence=path.resolve(options['evidence-dir']||process.env.EPISCOPE_UI_EVIDENCE_DIR||path.join(root,'docs','evidence'));
const metadataPath=path.join(evidence,`spark-optimisation-${stamp}.json`);
const started=Date.now(),captureDeadline=started+budgetMs-7000;
const result={status:'starting',captured_at:new Date().toISOString(),evidence_type:'Live controlled Spark query comparisons; aggregate queries over deidentified silver',expected_labels:expected,screenshots:[],queries:[],inspected_queries:[],missing_labels:expected.slice(),errors:[]};
let browser,verifiedApp=false,closed=false;

function remaining(cap=6000){return Math.max(1,Math.min(cap,captureDeadline-Date.now()));}
function withinCapture(){return Date.now()<captureDeadline-1200;}
function errorText(error){return String(error.message||error).split('\n')[0].slice(0,350);}
function saveMetadata(){result.missing_labels=expected.filter(label=>!result.queries.some(query=>query.label===label));fs.mkdirSync(evidence,{recursive:true});fs.writeFileSync(metadataPath,JSON.stringify(result,null,2)+'\n');}
function safeFile(label){return path.join(evidence,`spark-optimisation-${label}-${stamp}.png`);}
async function screenshot(page,label,{fullPage=false}={}){
  const target=safeFile(label);await page.screenshot({path:target,fullPage,timeout:remaining(4000)});
  const relative=path.relative(evidence,target).replaceAll('\\','/');result.screenshots.push(relative);return relative;
}
async function tableRows(page){
  return page.locator('tr').evaluateAll(rows=>rows.map(row=>({text:row.textContent.replace(/\s+/g,' ').trim(),links:Array.from(row.querySelectorAll('a'),a=>({text:a.textContent.trim(),href:a.href}))})));
}
function jobId(href){return Number(new URL(href).searchParams.get('id'));}
function operatorsFromDom(dom){
  const partitionMatches=Array.from(dom.matchAll(/PartitionFilters\s*:\s*(\[[^\]\r\n]*\])/g),match=>match[1]);
  return {operators:operatorNames.filter(name=>dom.includes(name)),sort_merge:dom.includes('SortMergeJoin'),broadcast_hash:dom.includes('BroadcastHashJoin'),opaque_python:dom.includes('BatchEvalPython'),native_year_partition_filter:partitionMatches.some(text=>text.includes('epi_year')&&text.trim()!=='[]'),empty_partition_filters:partitionMatches.some(text=>text.trim()==='[]')};
}
function classify(checks){
  if(checks.sort_merge)return expected[0];
  if(checks.broadcast_hash)return expected[1];
  if(checks.opaque_python)return expected[2];
  if(checks.native_year_partition_filter)return expected[3];
  return null;
}

(async()=>{
  if(!fs.existsSync(readyPath))throw new Error('Controlled benchmark ready marker is absent. No browser was launched.');
  const ready=JSON.parse(fs.readFileSync(readyPath,'utf8'));
  if(!ready.application_id||!ready.ui)throw new Error('Ready marker lacks application_id or UI address.');
  const reported=new URL(ready.ui),base=`http://127.0.0.1:${reported.port||4040}`;
  result.application_id_expected=String(ready.application_id);result.ui_reported=ready.ui;result.ui_used=base;
  if(ready.source_revision)result.source_revision=String(ready.source_revision);
  if(ready.performance_source_sha256)result.performance_source_sha256=String(ready.performance_source_sha256);
  if(ready.measurement_completed_at)result.measurement_completed_at=String(ready.measurement_completed_at);
  fs.mkdirSync(evidence,{recursive:true});
  const watchdog=setTimeout(()=>{result.status='timed_out';result.errors.push('Capture limit reached; no completion marker written.');saveMetadata();console.error(JSON.stringify({status:result.status,metadata:path.basename(metadataPath),available_screenshots:result.screenshots.length}));process.exit(1);},budgetMs);
  try{
    browser=await chromium.launch({headless:true,...(chromeExecutable?{executablePath:chromeExecutable}:{channel:chromeChannel}),timeout:remaining(60000)});
    result.browser_version=browser.version();result.playwright_version=require('playwright/package.json').version;
    const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
    page.setDefaultTimeout(remaining(4000));
    // Verify that the actual live UI belongs to the ready marker's application.
    try{
      const response=await page.request.get(base+'/api/v1/applications',{timeout:remaining(2500)});
      if(response.ok()){
        const applications=await response.json();
        const actual=Array.isArray(applications)?applications.find(app=>app.id===ready.application_id):null;
        if(actual){result.application_id_actual=actual.id;result.application_name=actual.name;result.application_verification='Live Spark applications API';verifiedApp=true;}
      }
    }catch(error){result.application_api_note=errorText(error);}
    if(!verifiedApp){
      await page.goto(base+'/environment/',{waitUntil:'domcontentloaded',timeout:remaining()});
      const rows=await tableRows(page),appId=rows.find(row=>/\bspark\.app\.id\b/.test(row.text)),appName=rows.find(row=>/\bspark\.app\.name\b/.test(row.text));
      const actualId=appId?appId.text.replace(/^spark\.app\.id\s*/,'').trim().split(/\s+/)[0]:null;
      if(actualId===String(ready.application_id)){result.application_id_actual=actualId;result.application_name=appName?appName.text.replace(/^spark\.app\.name\s*/,''):await page.title();result.application_verification='Live Spark Environment properties';verifiedApp=true;}
    }
    if(!verifiedApp)throw new Error('Live Spark application ID did not match the controlled benchmark ready marker.');
    if(!String(result.application_name).includes('EpiScope controlled optimisation evidence'))throw new Error('Live application name is not the controlled optimisation evidence run.');
    await page.goto(base+'/jobs/?completedJob.pageSize=200&completedJob.desc=true',{waitUntil:'domcontentloaded',timeout:remaining()});
    result.jobs_title=await page.title();
    const jobRows=await tableRows(page),jobLabels=new Map(),jobDescriptions=new Map();
    for(const row of jobRows){
      const labels=expected.filter(label=>row.text.includes(label));
      if(!labels.length)continue;
      const descriptions=labels.map(label=>row.text.match(new RegExp(label+':\\s*sample\\s+[123]/3'))?.[0]).filter(Boolean);
      for(const link of row.links.filter(link=>/\/jobs\/job\/\?id=\d+/.test(link.href))){const id=jobId(link.href);jobLabels.set(id,labels);jobDescriptions.set(id,descriptions);}
    }
    result.visible_controlled_jobs=Array.from(jobLabels,([id,labels])=>({job_id:id,labels,descriptions:jobDescriptions.get(id)}));
    await screenshot(page,'jobs',{fullPage:true});
    await page.goto(base+'/SQL/?completed.pageSize=200&completed.desc=true',{waitUntil:'domcontentloaded',timeout:remaining()});
    result.sql_title=await page.title();
    const sqlRows=await tableRows(page),byQuery=new Map();
    for(const row of sqlRows){
      for(const link of row.links){
        if(!/\/SQL\/execution\/\?id=\d+/.test(link.href))continue;
        // Spark 3.5.3 SQLExecution uses spark.job.description as the SQL
        // description when set; collect-at-performance.py is only a fallback.
        // Accept only these actual sample descriptions or that call site.
        const sqlLabels=expected.filter(label=>new RegExp('^'+label+':\\s*sample\\s+[123]/3$').test(link.text));
        const knownCallSite=/collect/i.test(link.text)&&/performance\.py/i.test(link.text);
        if(!sqlLabels.length&&!knownCallSite)continue;
        const id=jobId(link.href),jobs=row.links.filter(item=>/\/jobs\/job\/\?id=\d+/.test(item.href)).map(item=>jobId(item.href));
        byQuery.set(id,{query_id:id,description:link.text,url:link.href,job_ids:Array.from(new Set(jobs)),sql_labels:sqlLabels,job_labels:Array.from(new Set(jobs.flatMap(job=>jobLabels.get(job)||[])))});
      }
    }
    const candidates=Array.from(byQuery.values()).sort((a,b)=>a.query_id-b.query_id);
    result.controlled_sql_queries_found=candidates.length;
    result.candidate_query_ids=candidates.map(query=>query.query_id);
    await screenshot(page,'sql-summary',{fullPage:true});
    // Prefer real job-label matches. Otherwise inspect the last sample in each
    // chronological quarter of the 12 collect actions, then remaining queries.
    // Physical operators, not this ordering heuristic, determine each label.
    const preferred=[];
    for(const label of expected){const match=candidates.filter(query=>query.sql_labels.includes(label)||query.job_labels.includes(label)).at(-1);if(match)preferred.push(match);}
    if(candidates.length>=4)for(let i=1;i<=4;i++)preferred.push(candidates[Math.ceil(i*candidates.length/4)-1]);
    const queue=Array.from(new Map([...preferred,...candidates.slice().reverse()].map(query=>[query.query_id,query])).values());
    const captured=new Set();
    for(const query of queue){
      if(!withinCapture()||captured.size===expected.length)break;
      const declaredLabels=Array.from(new Set([...query.sql_labels,...query.job_labels]));
      if(declaredLabels.length===1&&captured.has(declaredLabels[0]))continue;
      try{
        await page.goto(query.url,{waitUntil:'domcontentloaded',timeout:remaining(4500)});
        // Reveal the Spark-provided description before inspecting partition
        // filters. Some Spark UIs keep it behind their native Details control.
        const details=page.getByText('Details',{exact:true});
        let detailsNote;
        if(await details.count())try{await details.last().click({timeout:remaining(1200)});}catch(error){detailsNote=errorText(error);}
        const dom=await page.locator('html').textContent();
        const checks=operatorsFromDom(dom),label=classify(checks);
        const inspected={query_id:query.query_id,job_ids:query.job_ids,sql_labels:query.sql_labels,job_labels:query.job_labels,physical_evidence:checks,classified_label:label};
        if(detailsNote)inspected.details_note=detailsNote;
        result.inspected_queries.push(inspected);
        if(!label||captured.has(label))continue;
        if(declaredLabels.length!==1||declaredLabels[0]!==label){inspected.note='Query/job labels and the observed physical operator did not uniquely agree; query not captured as that case.';continue;}
        if(/\bpatient_id\b|\bpatient_name\b|\bencounter_id\b|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY/.test(dom)){inspected.note='Unexpected direct identifier or private-key marker in plan DOM; screenshot skipped.';continue;}
        // No labels or plans are injected into the application UI.
        const image=await screenshot(page,label,{fullPage:true});
        result.queries.push({...query,label,physical_evidence:checks,ui_title:await page.title(),screenshot:image,plan_dom_sha256:crypto.createHash('sha256').update(dom).digest('hex')});
        captured.add(label);
      }catch(error){result.errors.push(`Query ${query.query_id}: ${errorText(error)}`);}
    }
    result.missing_labels=expected.filter(label=>!captured.has(label));
    result.status=captured.size===expected.length?'complete':'partial';
    if(result.status==='partial')result.note='Only located live captures are included. Missing cases have not been fabricated.';
  }catch(error){result.status='failed';result.errors.push(errorText(error));}
  finally{
    if(browser)try{await browser.close();closed=true;}catch(error){result.errors.push('Browser close: '+errorText(error));}
    result.browser_closed=closed;result.elapsed_seconds=(Date.now()-started)/1000;
    saveMetadata();
    const usable=verifiedApp&&closed&&result.screenshots.length>0&&result.status!=='failed';
    if(usable){fs.mkdirSync(path.dirname(donePath),{recursive:true});fs.writeFileSync(donePath,JSON.stringify({status:result.status,metadata:path.basename(metadataPath),application_id:result.application_id_actual,captured_labels:result.queries.map(query=>query.label),missing_labels:result.missing_labels})+'\n');}
    clearTimeout(watchdog);
    console.log(JSON.stringify({status:result.status,application_id:result.application_id_actual,metadata:path.basename(metadataPath),captured_labels:result.queries.map(query=>query.label),missing_labels:result.missing_labels,browser_closed:closed,elapsed_seconds:result.elapsed_seconds},null,2));
    if(!usable)process.exitCode=1;
  }
})().catch(error=>{console.error(errorText(error));process.exitCode=1;});
